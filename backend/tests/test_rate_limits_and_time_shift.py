"""A run's time shift dates, its questions waiting out an LLM rate limit, and reruns."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.models.models import EvalResult, EvalRun, EvalStatus, GoldenQuestion
from sqlmodel import select

from app.routers.orchestration import _merge_rerun_results, _save_job_state
from app.services.time_shift import normalize_time_shift, time_shift_offsets

ORCH = "app.routers.orchestration"
_next_connection = iter(range(4400, 4500))


# ── time shift ──────────────────────────────────────────────────────────────


def test_time_shift_entries_are_checked_and_deduplicated():
    assert normalize_time_shift(["-1", " -7 ", "2023-10-07", "-7"]) == [
        "-1",
        "-7",
        "2023-10-07",
    ]
    # nothing chosen, or exactly the defaults: the evaluation service's own
    assert normalize_time_shift(None) is None
    assert normalize_time_shift(["-60", "-30", "-14", "-7", "-1"]) is None


@pytest.mark.parametrize("bad", ["abc", "0", "2023-13-40", "99999"])
def test_invalid_time_shift_is_rejected(bad):
    with pytest.raises(ValueError):
        normalize_time_shift([bad])


def test_today_is_rejected():
    with pytest.raises(ValueError):
        normalize_time_shift([date.today().isoformat()])


def test_dates_become_days_back_from_the_job_start():
    today = date(2026, 11, 26)
    assert time_shift_offsets(["-7", "2023-10-07", "2026-04-08"], today) == [
        -7,
        -1146,
        -232,
    ]
    assert time_shift_offsets(None, today) is None


def _connection_run(client, params):
    pairs = [{"question": "q?", "sql_statement": "SELECT 1", "tags": []}]
    with (
        patch(f"{ORCH}.fetch_jeen_sql_pairs_with_tags", AsyncMock(return_value=pairs)),
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}._run_dataset_pipeline"),
    ):
        langfuse.enabled = False
        return client.post(
            "/api/evaluations/run-connection",
            params={"connection_id": next(_next_connection), **params},
        )


def test_run_keeps_its_time_shift(client, db_session):
    response = _connection_run(client, {"time_shift": ["-7", "2023-10-07"]})
    assert response.status_code == 202, response.text
    assert db_session.get(EvalRun, response.json()["id"]).time_shift == [
        "-7",
        "2023-10-07",
    ]
    report = client.get(f"/api/evaluations/runs/{response.json()['id']}/report").json()
    assert report["time_shift"] == ["-7", "2023-10-07"]


def test_run_with_an_invalid_time_shift_is_rejected(client):
    response = _connection_run(client, {"time_shift": ["yesterday"]})
    assert response.status_code == 400
    assert "Time shift" in response.json()["detail"]


# ── rate limits ─────────────────────────────────────────────────────────────


def _run(db_session, **fields) -> EvalRun:
    run = EvalRun(
        triggered_by="user",
        connection_id=next(_next_connection),
        dataset_id="text2sql_connection_x",
        total_questions=2,
        status=fields.pop("status", EvalStatus.running),
        **fields,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


WAITING = {
    "question_id": "item:q-1",
    "question": "How many?",
    "stage": "agent",
    "retry": 1,
    "max_retries": 3,
    "retry_at": "2026-11-26T10:00:20+00:00",
    "error": "429",
}


def test_job_state_is_kept_and_reported(client, db_session):
    run = _run(db_session)
    _save_job_state(run, {"job_id": "job-1", "rate_limited": [WAITING]}, db_session)

    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    assert report["rate_limited"] == [{**WAITING, "question_id": "q-1"}]
    db_session.refresh(run)
    assert run.eval_job_id == "job-1"


def test_stopping_a_question_calls_the_evaluation_service(client, db_session):
    run = _run(db_session, eval_job_id="job-1", rate_limited=[WAITING])
    with patch(
        f"{ORCH}.requests.post", return_value=MagicMock(status_code=200)
    ) as post:
        response = client.post(
            f"/api/evaluations/runs/{run.id}/questions/q-1/stop-retry"
        )

    assert response.status_code == 200, response.text
    assert post.call_args.args[0].endswith("/jobs/job-1/questions/item:q-1/stop-retry")
    db_session.refresh(run)
    assert run.rate_limited is None


def test_stopping_a_question_that_isnt_waiting_is_a_conflict(client, db_session):
    run = _run(db_session, eval_job_id="job-1", rate_limited=[WAITING])
    response = client.post(f"/api/evaluations/runs/{run.id}/questions/other/stop-retry")
    assert response.status_code == 409


def _result(run, qid, status, error=None, contains=1.0, time_shift=None):
    scores = {"contains_accuracy": contains}
    if time_shift is not None:
        scores["time_shift"] = time_shift
    return EvalResult(
        run_id=run.id,
        question_id=qid,
        score=contains,
        status=status,
        error_type=error,
        scores=scores,
    )


def test_rerun_takes_the_rate_limited_questions(client, db_session):
    run = _run(db_session, status=EvalStatus.completed)
    db_session.add(_result(run, "q-ok", "pass"))
    db_session.add(_result(run, "q-limited", "fail", "rate_limited: agent (...)", 0.0))
    db_session.commit()

    with patch(f"{ORCH}._rerun_pipeline") as pipeline:
        response = client.post(f"/api/evaluations/runs/{run.id}/rerun")

    assert response.status_code == 202, response.text
    assert pipeline.call_args.args == (run.id, ["q-limited"])
    db_session.refresh(run)
    assert run.status == EvalStatus.running and run.rerun_question_ids == ["q-limited"]
    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    statuses = {q["question_id"]: q["status"] for q in report["per_question"]}
    assert statuses == {"q-ok": "pass", "q-limited": "rerunning"}


def test_rerun_of_a_running_run_is_a_conflict(client, db_session):
    run = _run(db_session)
    assert client.post(f"/api/evaluations/runs/{run.id}/rerun").status_code == 409


def test_rerun_without_rate_limited_questions_is_not_found(client, db_session):
    run = _run(db_session, status=EvalStatus.completed)
    db_session.add(_result(run, "q-ok", "pass"))
    db_session.commit()
    assert client.post(f"/api/evaluations/runs/{run.id}/rerun").status_code == 404


def test_rerun_results_replace_the_old_ones_and_rescore(db_session):
    question = GoldenQuestion(connection_id=1, question="q?", expected_sql="SELECT 1")
    other = GoldenQuestion(connection_id=1, question="o?", expected_sql="SELECT 2")
    db_session.add(question)
    db_session.add(other)
    db_session.commit()
    run = _run(
        db_session,
        rerun_question_ids=[question.id],
        failure_breakdown={"rate_limited": 1, "timeout": 0},
    )
    db_session.add(_result(run, other.id, "pass", contains=1.0, time_shift=1.0))
    db_session.add(_result(run, question.id, "fail", "rate_limited: agent (...)", 0.0))
    db_session.commit()

    case = SimpleNamespace(
        question_id=question.id,
        scores={"contains_accuracy": 1.0, "time_shift": 0.5},
        error=None,
        question="q?",
        expected_sql="SELECT 1",
        generated_sql="SELECT 1",
    )
    _merge_rerun_results(run, [case], db_session)
    db_session.commit()

    results = {
        r.question_id: r
        for r in db_session.exec(select(EvalResult).where(EvalResult.run_id == run.id))
    }
    assert (
        results[question.id].status == "pass"
        and results[question.id].error_type is None
    )
    assert run.status == EvalStatus.completed and run.rerun_question_ids is None
    assert run.pass_rate == 1.0
    assert run.final_score == 0.75  # time shift: (1.0 + 0.5) / 2
    assert "rate_limited" not in run.failure_breakdown


# ── chosen questions ────────────────────────────────────────────────────────


def _run_with_pairs(client, connection_id, params):
    pairs = [
        {
            "question": f"pick{i}?",
            "sql_statement": f"SELECT {i}",
            "tags": ["t"] if i else [],
        }
        for i in range(4)
    ]
    with (
        patch(f"{ORCH}.fetch_jeen_sql_pairs_with_tags", AsyncMock(return_value=pairs)),
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}._run_dataset_pipeline") as pipeline,
    ):
        langfuse.enabled = False
        response = client.post(
            "/api/evaluations/run-connection",
            params={"connection_id": connection_id, **params},
        )
    return response, pipeline


def test_run_only_the_chosen_questions(client, db_session):
    connection_id = next(_next_connection)
    # creates the connection's golden questions
    _run_with_pairs(client, connection_id, {})
    ids = {
        q.question: q.id
        for q in db_session.exec(
            select(GoldenQuestion).where(GoldenQuestion.connection_id == connection_id)
        )
    }
    chosen = [ids["pick1?"], ids["pick3?"]]

    response, pipeline = _run_with_pairs(
        client, connection_id, {"question_ids": chosen}
    )

    assert response.status_code == 202, response.text
    assert response.json()["total_questions"] == 2
    assert sorted(pipeline.call_args.args[3]) == sorted(chosen)


def test_chosen_questions_and_tags_both_apply(client, db_session):
    connection_id = next(_next_connection)
    _run_with_pairs(client, connection_id, {})
    ids = {
        q.question: q.id
        for q in db_session.exec(
            select(GoldenQuestion).where(GoldenQuestion.connection_id == connection_id)
        )
    }
    # pick0 has no tag "t": only pick2 is left
    response, pipeline = _run_with_pairs(
        client,
        connection_id,
        {"question_ids": [ids["pick0?"], ids["pick2?"]], "tags": ["t"]},
    )
    assert response.status_code == 202
    assert pipeline.call_args.args[3] == [ids["pick2?"]]


def test_unknown_chosen_questions_are_rejected(client):
    response, pipeline = _run_with_pairs(
        client, next(_next_connection), {"question_ids": ["not-a-question"]}
    )
    assert response.status_code == 404
    pipeline.assert_not_called()
