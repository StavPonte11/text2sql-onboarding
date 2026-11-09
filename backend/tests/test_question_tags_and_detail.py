"""Running a connection by question tag, and the per-question detail."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.models.models import EvalResult, EvalRun, EvalStatus, GoldenQuestion

from app.services.eval_question_trace import fetch_question_trace
from app.services.jeen_metadata import fetch_jeen_sql_pairs_with_tags

_next_connection = iter(range(4300, 4400))
ORCH = "app.routers.orchestration"


def _pairs(*tagged):
    return [
        {"question": f"q{i}?", "sql_statement": f"SELECT {i}", "tags": tags}
        for i, tags in enumerate(tagged)
    ]


def _run_connection(client, connection_id, pairs, **params):
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


def test_run_connection_with_tags_selects_any_matching_question(client, db_session):
    connection_id = next(_next_connection)
    response, pipeline = _run_connection(
        client,
        connection_id,
        _pairs(["validation"], ["test"], ["other"], ["test", "validation"]),
        tags=["validation", "test"],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["total_questions"] == 3
    assert body["question_tags"] == ["validation", "test"]

    run = db_session.get(EvalRun, body["id"])
    selected = {db_session.get(GoldenQuestion, qid).question for qid in run.question_ids}
    assert selected == {"q0?", "q1?", "q3?"}
    assert pipeline.call_args.args[3] == run.question_ids


def test_run_connection_without_tags_runs_all(client):
    response, pipeline = _run_connection(
        client, next(_next_connection), _pairs(["a"], ["b"], [])
    )
    assert response.status_code == 202
    assert response.json()["total_questions"] == 3
    assert response.json()["question_tags"] is None
    assert pipeline.call_args.args[3] is None


def test_run_connection_with_unknown_tag_is_rejected(client):
    response, pipeline = _run_connection(
        client, next(_next_connection), _pairs(["a"]), tags=["missing"]
    )
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]
    pipeline.assert_not_called()


def test_report_lists_only_the_runs_questions_as_pending(client, db_session):
    connection_id = next(_next_connection)
    response, _ = _run_connection(
        client, connection_id, _pairs(["keep"], ["other"]), tags=["keep"]
    )
    report = client.get(f"/api/evaluations/runs/{response.json()['id']}/report").json()
    assert [q["question"] for q in report["per_question"]] == ["q0?"]


@pytest.mark.asyncio
async def test_tags_are_normalized_and_filled_from_knowledge_questions():
    pairs = [
        {"question": "a?", "tags": [{"name": "x"}, " y ", "y"]},
        {"question": "b?", "tags": "m, n"},
        {"question": "c?"},
        {"question": "d?"},
    ]
    knowledge = [{"question": "c?", "tags": ["from-knowledge"]}]
    with (
        patch("app.services.jeen_metadata.fetch_jeen_sql_pairs", AsyncMock(return_value=pairs)),
        patch(
            "app.services.jeen_metadata.fetch_jeen_knowledge_questions",
            AsyncMock(return_value=knowledge),
        ),
    ):
        result = await fetch_jeen_sql_pairs_with_tags(1)
    assert [p["tags"] for p in result] == [["x", "y"], ["m", "n"], ["from-knowledge"], []]


@pytest.mark.asyncio
async def test_pairs_survive_a_knowledge_questions_failure():
    with (
        patch(
            "app.services.jeen_metadata.fetch_jeen_sql_pairs",
            AsyncMock(return_value=[{"question": "a?"}]),
        ),
        patch(
            "app.services.jeen_metadata.fetch_jeen_knowledge_questions",
            AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        result = await fetch_jeen_sql_pairs_with_tags(1)
    assert result == [{"question": "a?", "tags": []}]


SAMPLE = {
    "id": "sample",
    "traceId": "trace1",
    "projectId": "proj",
    "startTime": "2026-10-26T12:50:51Z",
    "name": "eval-sample:q1",
    "metadata": {"total_latency_ms": 100, "scope": {"name": "sdk"}, "resourceAttributes": {}},
    "output": (
        '{"generated_sql": "SELECT 2", "expected_sql": "SELECT 1", "succeeded": false,'
        ' "error": "boom", "evaluations": {'
        '"contains_accuracy": {"score": 0, "passed": false, "applicable": true,'
        ' "details": {"reason": "no_match"}},'
        '"time_shift": {"score": 0, "passed": false, "applicable": false}}}'
    ),
}
CHILDREN = [
    SAMPLE,
    {
        "name": "expected_sql_execution",
        "parentObservationId": "sample",
        "input": '{"sql": "SELECT 1"}',
        "output": '{"success": true, "columns": ["a"], "rows": [[1]], "row_count": 1}',
        "metadata": {"execution_time_ms": 12.5},
    },
    {"name": "expected_sql_execution", "parentObservationId": None, "output": '{"wrapper": 1}'},
    {
        "name": "agent_execution",
        "parentObservationId": "sample",
        "output": '{"status": "error"}',
    },
]
SCORES = [
    {"name": "contains_accuracy", "value": 0},
    {"name": "agent_sql_generation_seconds", "value": 283.83},
]


def _langfuse(*pages):
    responses = [MagicMock(**{"json.return_value": {"data": page}}) for page in pages]
    return patch("app.services.eval_question_trace.requests.get", side_effect=responses)


def test_fetch_question_trace_collects_runs_scores_and_metadata():
    start = datetime(2026, 10, 26, 12, 0)
    with _langfuse([SAMPLE], CHILDREN, SCORES) as get:
        trace = fetch_question_trace("q1", start, start + timedelta(hours=2))

    assert get.call_args_list[0].kwargs["params"]["name"] == "eval-sample:q1"
    assert get.call_args_list[0].kwargs["params"]["fromStartTime"] == "2026-10-26T12:00:00Z"
    assert trace["langfuse_url"].endswith("/project/proj/traces/trace1")
    assert trace["expected_run"] == {
        "sql": "SELECT 1",
        "execution_time_ms": 12.5,
        "success": True,
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
    }
    assert trace["generated_run"] is None
    assert trace["agent_status"] == "error"
    assert trace["metadata"] == {"total_latency_ms": 100}
    assert {s["name"]: s["applicable"] for s in trace["scores"]} == {
        "contains_accuracy": True,
        "time_shift": False,
        "agent_sql_generation_seconds": True,
    }


def test_fetch_question_trace_without_a_record():
    now = datetime(2026, 10, 26, 12, 0)
    with _langfuse([]):
        assert fetch_question_trace("q1", now, now) is None


def test_fetch_question_trace_retries_when_rate_limited():
    limited = MagicMock(status_code=429)
    empty = MagicMock(status_code=200, **{"json.return_value": {"data": []}})
    now = datetime(2026, 10, 26, 12, 0)
    with (
        patch("app.services.eval_question_trace.time.sleep"),
        patch("app.services.eval_question_trace.requests.get", side_effect=[limited, empty]) as get,
    ):
        assert fetch_question_trace("q1", now, now) is None
    assert get.call_count == 2


def _finished_question(db_session):
    question = GoldenQuestion(connection_id=next(_next_connection), question="q?", expected_sql="SELECT 1")
    run = EvalRun(table_id=None, total_questions=1, status=EvalStatus.completed)
    db_session.add_all([question, run])
    db_session.commit()
    db_session.refresh(question)
    db_session.refresh(run)
    db_session.add(
        EvalResult(run_id=run.id, question_id=question.id, score=0.0, status="fail", error_type="boom")
    )
    db_session.commit()
    return run, question


def test_question_endpoint_returns_trace(client, db_session):
    run, question = _finished_question(db_session)
    with (
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}.fetch_question_trace", return_value={"trace_id": "t"}),
    ):
        langfuse.enabled = True
        body = client.get(f"/api/evaluations/runs/{run.id}/questions/{question.id}").json()
    assert body["question"] == "q?"
    assert body["status"] == "fail"
    assert body["trace"] == {"trace_id": "t"}
    assert body["trace_unavailable_reason"] is None


def test_question_endpoint_still_answers_when_langfuse_fails(client, db_session):
    import requests

    run, question = _finished_question(db_session)
    with (
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}.fetch_question_trace", side_effect=requests.ConnectionError("x")),
    ):
        langfuse.enabled = True
        response = client.get(f"/api/evaluations/runs/{run.id}/questions/{question.id}")
    assert response.status_code == 200
    assert response.json()["trace"] is None
    assert response.json()["failure_type"] == "boom"
    assert "Langfuse" in response.json()["trace_unavailable_reason"]


def test_question_endpoint_unknown_question(client, db_session):
    run, _ = _finished_question(db_session)
    assert client.get(f"/api/evaluations/runs/{run.id}/questions/nope").status_code == 404
