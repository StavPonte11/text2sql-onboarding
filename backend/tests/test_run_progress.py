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
