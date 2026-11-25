"""Live progress: finished questions are saved and shown while a run is going."""

from core.models.models import EvalResult, EvalRun, EvalStatus, GoldenQuestion
from sqlmodel import select

from app.routers.evaluation import save_case_results

_next_connection = iter(range(4200, 4300))


def _connection_run(db_session, n_questions=3):
    connection_id = next(_next_connection)  # tests share one database
    questions = [
        GoldenQuestion(connection_id=connection_id, question=f"q{i}?", expected_sql="SELECT 1")
        for i in range(n_questions)
    ]
    db_session.add_all(questions)
    run = EvalRun(
        table_id=None,
        total_questions=n_questions,
        triggered_by="text2sql_production",
        connection_id=connection_id,
        status=EvalStatus.running,
    )
    db_session.add(run)
    db_session.commit()
    for q in questions:
        db_session.refresh(q)
    db_session.refresh(run)
    return run, questions


def test_save_case_results_skips_already_saved_and_unknown(db_session):
    run, (q0, q1, _) = _connection_run(db_session)
    cases = [
        {"question_id": q0.id, "scores": {"contains_accuracy": 1.0}},
        {"question_id": "bird-123", "scores": {"contains_accuracy": 1.0}},  # not a golden q
    ]
    assert save_case_results(run.id, cases, db_session) == {q0.id}
    cases.append({"question_id": q1.id, "scores": {"contains_accuracy": 0.0}, "error": "x"})
    assert save_case_results(run.id, cases, db_session, skip_question_ids={q0.id}) == {q1.id}
    db_session.commit()

    rows = db_session.exec(select(EvalResult).where(EvalResult.run_id == run.id)).all()
    assert {(r.question_id, r.status) for r in rows} == {(q0.id, "pass"), (q1.id, "fail")}


def test_report_and_run_list_show_progress_while_running(client, db_session):
    run, (q0, q1, q2) = _connection_run(db_session)
    save_case_results(run.id, [{"question_id": q1.id, "scores": {"contains_accuracy": 1.0}}], db_session)
    db_session.commit()

    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    assert report["completed_questions"] == 1
    assert report["total_questions"] == 3
    statuses = [(q["question_id"], q["status"]) for q in report["per_question"]]
    assert statuses[0] == (q1.id, "pass")  # finished first
    assert sorted(s for _, s in statuses[1:]) == ["pending", "pending"]

    runs = client.get("/api/evaluations/runs", params={"connection_id": run.connection_id}).json()
    assert runs[0]["completed_questions"] == 1


def test_each_result_keeps_its_scores_for_the_report(client, db_session):
    run, (q0, q1, _) = _connection_run(db_session)
    save_case_results(
        run.id,
        [
            {"question_id": q0.id, "scores": {"contains_accuracy": 1.0, "time_shift": 0.5}},
            {"question_id": q1.id, "scores": {"contains_accuracy": 0.0}},  # time shift N/A
        ],
        db_session,
    )
    db_session.commit()

    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    scores = {q["question_id"]: q["scores"] for q in report["per_question"]}
    assert scores[q0.id] == {"contains_accuracy": 1.0, "time_shift": 0.5}
    assert scores[q1.id] == {"contains_accuracy": 0.0}


def test_bird_run_takes_its_question_count_from_the_evaluation_service(
    db_session, test_engine
):
    """A BIRD run starts with 0 questions; the service's total_items fills it in."""
    from unittest.mock import patch

    from app.routers.orchestration import _run_dataset_pipeline

    run = EvalRun(
        table_id=None,
        total_questions=0,
        triggered_by="bird",
        connection_id=next(_next_connection),
        connection_name="bird",
        status=EvalStatus.running,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    def fake_call(req, is_cancelled=None, on_progress=None):
        on_progress({"status": "running", "total_items": None, "completed_cases": []})
        on_progress({"status": "running", "total_items": 178, "completed_cases": []})
        raise RuntimeError("stop")

    with (
        patch("app.routers.orchestration.engine", test_engine),
        patch("app.routers.orchestration._call_run_new_evaluation", side_effect=fake_call),
    ):
        _run_dataset_pipeline("bird", run.id)

    db_session.expire_all()
    assert db_session.get(EvalRun, run.id).total_questions == 178


def _bird_run(db_session):
    run = EvalRun(
        table_id=None,
        total_questions=150,
        triggered_by="bird",
        connection_id=next(_next_connection),
        connection_name="bird",
        status=EvalStatus.running,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


_BIRD_CASE = {
    "question_id": "bird-5913",
    "question": "What is the publication date of the book with the most pages?",
    "expected_sql": "SELECT publication_date FROM bird_books.book LIMIT 1",
    "generated_sql": "SELECT publication_date FROM bird_books.book LIMIT 1",
    "scores": {"contains_accuracy": 1.0, "execution_accuracy": 1.0},
}


def test_bird_results_are_saved_and_shown_while_the_run_is_going(client, db_session):
    """BIRD questions aren't golden questions, but a BIRD run still lists them."""
    run = _bird_run(db_session)
    assert save_case_results(run.id, [_BIRD_CASE], db_session, benchmark=True) == {
        "bird-5913"
    }
    db_session.commit()

    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    assert report["completed_questions"] == 1
    [row] = report["per_question"]
    assert (row["question_id"], row["status"]) == ("bird-5913", "pass")
    assert row["question"] == _BIRD_CASE["question"]

    detail = client.get(f"/api/evaluations/runs/{run.id}/questions/bird-5913")
    assert detail.status_code == 200
    assert detail.json()["question"] == _BIRD_CASE["question"]
    assert detail.json()["expected_sql"] == _BIRD_CASE["expected_sql"]


def test_bird_pipeline_saves_finished_questions_as_they_come(db_session, test_engine):
    from unittest.mock import patch

    from app.routers.orchestration import _run_dataset_pipeline

    run = _bird_run(db_session)

    def fake_call(req, is_cancelled=None, on_progress=None):
        on_progress({"total_items": 150, "completed_cases": [_BIRD_CASE]})
        raise RuntimeError("stop")

    with (
        patch("app.routers.orchestration.engine", test_engine),
        patch("app.routers.orchestration._call_run_new_evaluation", side_effect=fake_call),
    ):
        _run_dataset_pipeline("bird", run.id)

    rows = db_session.exec(select(EvalResult).where(EvalResult.run_id == run.id)).all()
    assert [(r.question_id, r.question) for r in rows] == [
        ("bird-5913", _BIRD_CASE["question"])
    ]


def test_deleting_a_golden_question_deletes_its_results(client, db_session):
    run, (q0, _, _) = _connection_run(db_session)
    question_id, connection_id = q0.id, run.connection_id
    save_case_results(run.id, [{"question_id": question_id, "scores": {}}], db_session)
    db_session.commit()

    response = client.delete(f"/api/connections/{connection_id}/questions/{question_id}")
    assert response.status_code == 204
    db_session.expire_all()
    assert not db_session.exec(
        select(EvalResult).where(EvalResult.question_id == question_id)
    ).all()
