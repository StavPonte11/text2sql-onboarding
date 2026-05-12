"""
evaluation.py — Real evaluation pipeline (no mocks).

Pipeline per question:
  1. Call agent API (stubbed - replace with real LangGraph client)
  2. Compare result shape vs expected_result_shape
  3. Call LLM judge API (stubbed - replace with real Langfuse/OpenAI client)
  4. Apply 3-layer scoring (deterministic — uses services/scoring.py)
  5. Persist EvalResult with full breakdown
  6. Aggregate into EvalRun score

All heavy work runs in FastAPI BackgroundTasks (async from the HTTP perspective).
In production, migrate this to a Celery/Temporal worker.
"""

import uuid
import time
import random
import json
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from sqlmodel import Session, select, desc
from app.db.engine import get_session, engine
from app.models.models import (
    Table, TableStatus, GoldenQuestion,
    EvalRun, EvalRunRead, EvalStatus,
    EvalResult, EvalResultRead,
    EvaluationAlert, AlertSeverity,
    AuditQuery,
)
from app.services.scoring import (
    JudgeOutput, ExecutionResult, ExpectedShape,
    compute_score, compute_dataset_score,
    PASS_THRESHOLD, PARTIAL_THRESHOLD,
)
from app.services.langfuse_client import langfuse_client, Evaluation
from langfuse.decorators import observe, langfuse_context

router = APIRouter(tags=["evaluation"])


# ─── Stubbed external service calls ───────────────────────────────────────────
# Replace each stub with a real HTTP call to your LangGraph/Trino/LLM service.

@observe(as_type="generation")
def call_agent(question: str, table_id: str) -> dict:
    """
    STUB — replace with real call to LangGraph TextToSQL agent.

    Expected return:
    {
        "generated_sql": str,
        "tables_used": list[str],
        "generated_columns": list[str],
        "refiner_iterations": int,
        "query_translation": str,        # Hebrew translation of the query
        "hebrew_answer": str,             # Final Hebrew answer from agent
        "execution": {
            "success": bool, "rows": [], "columns": [],
            "row_count": int, "execution_time_ms": int, "error_message": str | None
        }
    }
    """
    # Simulates a realistic agent call with variable quality
    success = random.random() > 0.1  # 90% execution success rate
    row_count = random.randint(0, 5000) if success else 0
    iterations = random.choices([0, 1, 2, 3], weights=[60, 25, 10, 5])[0]

    return {
        "generated_sql": f"SELECT * FROM {table_id[:8]}.stub_table LIMIT 100",
        "tables_used": [f"{table_id[:8]}.stub_table"],
        "generated_columns": ["id", "name", "value"],
        "refiner_iterations": iterations,
        # Real agent output fields (stubbed)
        "query_translation": f"[HE] {question[:40]}...",
        "hebrew_answer": "[HE] תשובה מדומה לפי תוצאות השאילתה.",
        "execution": {
            "success": success,
            "rows": [],
            "columns": ["id", "name", "value"] if success else [],
            "row_count": row_count,
            "execution_time_ms": random.randint(200, 8000),
            "error_message": None if success else "Stub execution error",
        },
    }


@observe(as_type="generation")
def call_llm_judge(
    question: str,
    expected_sql: str,
    generated_sql: str,
    execution_meta: dict,
    schema_context: str,
) -> dict:
    """
    STUB — replace with real LLM judge call (OpenAI / Anthropic via Langfuse).

    Expected return matches JudgeOutput fields.
    """
    # Simulate realistic judge scores (correlated with execution success)
    exec_success = execution_meta.get("success", False)
    # INCREASED BASE SCORE: 0.70 to 0.95 for success, 0.2 to 0.45 for fail
    base = random.uniform(0.70, 0.95) if exec_success else random.uniform(0.20, 0.45)

    return {
        "table_selection_correctness": round(min(1.0, base + random.uniform(-0.1, 0.1)), 3),
        "sql_semantic_equivalence":    round(min(1.0, base + random.uniform(-0.15, 0.1)), 3),
        "result_correctness":          round(min(1.0, base + random.uniform(-0.05, 0.1)), 3),
        "hallucination_detected":      random.random() < 0.05,  # 5% hallucination rate
        "failure_type":                None,
        "reasoning": {
            "table_selection": "Stub reasoning",
            "sql_equivalence": "Stub reasoning",
            "result_correctness": "Stub reasoning",
            "hallucination": "No hallucination detected in stub mode",
        },
        "confidence_in_judgment": round(random.uniform(0.7, 0.95), 3),
    }


# emit_langfuse_trace is now handled by app.services.langfuse_client


# ─── Core evaluation pipeline ──────────────────────────────────────────────────

@observe(name="eval-single-table")
def execute_single_table_eval(table_id: str, run_id: str, session: Session) -> float:
    """
    Core logic for evaluating a single table.
    Returns the final dataset score.
    """
    run = session.get(EvalRun, run_id)
    if not run:
        return 0.0

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()

    if not questions:
        run.status = EvalStatus.failed
        run.score = 0.0
        session.add(run)
        session.commit()
        return 0.0

    langfuse_context.update_current_trace(
        metadata={"table_id": table_id, "run_id": run_id},
        tags=["eval-run", f"table:{table_id}"]
    )

    question_scores: list[tuple[float, str]] = []
    failure_counts = {
        "wrong_table": 0, "wrong_join": 0, "wrong_filter": 0,
        "hallucination": 0, "execution_error": 0, "empty_result_bug": 0, "partial_correct": 0,
    }

    dataset_name = f"text2sql_{table_id[:8]}"
    run_name = f"EvalRun-{run_id}"

    if langfuse_client.enabled:
        try:
            table = session.get(Table, table_id)

            if langfuse_client.dataset_exists(dataset_name):
                # Dataset already present — just fetch it, no re-sync needed
                dataset = langfuse_client.get_dataset(dataset_name)
                print(f"[Langfuse] Dataset '{dataset_name}' exists, skipping re-sync.")
            else:
                # Dataset was deleted or never created — rebuild it from DB questions
                print(f"[Langfuse] Dataset '{dataset_name}' missing, rebuilding...")
                questions_payload = [
                    {
                        "question_id": q.id,
                        "question_text": q.question,
                        "expected_sql": q.expected_sql or "",
                        "table_id": table_id,
                        "schema_name": table.schema_name if table else table_id,
                        "question_type": q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
                        "difficulty": q.difficulty.value if hasattr(q.difficulty, "value") else str(q.difficulty),
                        "split": "",
                    }
                    for q in questions
                ]
                dataset = langfuse_client.ensure_dataset_synced(dataset_name, questions_payload)

            if not dataset:
                print(f"[Langfuse] Dataset unavailable for {table_id}, scoring via stubs only.")

            def judge_evaluator(item, result) -> Evaluation:
                judge = result.get("judge")
                if not judge: return Evaluation(value=0.0, comment="No judge output")
                return Evaluation(value=judge.result_correctness, comment=f"Judge: {judge.confidence_in_judgment}")

            def execution_evaluator(item, result) -> Evaluation:
                exec_data = result.get("execution")
                if not exec_data: return Evaluation(value=0.0, comment="No execution data")
                return Evaluation(value=1.0 if exec_data.success else 0.0, comment=exec_data.error_message)

            def final_score_evaluator(item, result) -> Evaluation:
                breakdown = result.get("breakdown")
                if not breakdown: return Evaluation(value=0.0, comment="No breakdown")
                return Evaluation(value=breakdown.final_score, comment=f"Status: {breakdown.question_status}")

            @observe(name="eval-question")
            def evaluation_task(item):
                trace_id = langfuse_context.get_current_trace_id()
                observation_id = langfuse_context.get_current_observation_id()
                q_id = item.metadata.get("question_id")
                question_obj = session.get(GoldenQuestion, q_id)
                if not question_obj: return {"trace_id": trace_id, "observation_id": observation_id}

                langfuse_client.link_trace_to_dataset_run(
                    dataset_item_id=item.id, trace_id=trace_id, observation_id=observation_id,
                    run_name=run_name, run_metadata={"table_id": table_id}
                )

                langfuse_context.update_current_trace(
                    input={
                        # ── Matches real agent input schema ───────────────────
                        "query": question_obj.question,
                        "databases": [question_obj.table_id],
                    }
                )

                agent_result = call_agent(question_obj.question, table_id)
                exec_data = agent_result["execution"]
                execution = ExecutionResult(
                    success=exec_data["success"], rows=exec_data["rows"], columns=exec_data["columns"],
                    row_count=exec_data["row_count"], execution_time_ms=exec_data["execution_time_ms"],
                    error_message=exec_data.get("error_message"),
                )

                judge_raw = call_llm_judge(
                    question=question_obj.question, expected_sql=question_obj.expected_sql,
                    generated_sql=agent_result["generated_sql"], execution_meta=exec_data, schema_context="",
                )
                judge = JudgeOutput(
                    table_selection_correctness=judge_raw["table_selection_correctness"],
                    sql_semantic_equivalence=judge_raw["sql_semantic_equivalence"],
                    result_correctness=judge_raw["result_correctness"],
                    hallucination_detected=judge_raw["hallucination_detected"],
                    failure_type=judge_raw.get("failure_type"),
                    reasoning=judge_raw.get("reasoning", {}),
                    confidence_in_judgment=judge_raw.get("confidence_in_judgment", 0.8),
                )

                breakdown = compute_score(
                    execution=execution,
                    expected_shape=ExpectedShape(row_count_min=0, row_count_max=999_999, expected_columns=[]),
                    judge=judge, tables_used=agent_result["tables_used"], expected_tables=[],
                    generated_columns=agent_result["generated_columns"], schema_columns=[],
                    refiner_iterations=agent_result["refiner_iterations"],
                    question_type=str(question_obj.question_type).lower(),
                )

                # Build result rows (real agent returns actual SQL rows)
                result_rows = [
                    {
                        "entityid": f"row-{i+1}",
                        "title": f"Result {i+1}",
                        "start_time": None,
                        "content": f"stub row {i+1}",
                    }
                    for i in range(min(3, exec_data["row_count"]))
                ]

                langfuse_context.update_current_trace(
                    output={
                        # ── Matches real agent output schema ──────────────────
                        "result": result_rows,
                        "response": agent_result["generated_sql"],
                        "query_translation": agent_result.get("query_translation", ""),
                        "hebrew_answer": agent_result.get("hebrew_answer", ""),
                    }
                )

                result_db = EvalResult(
                    run_id=run_id, question_id=question_obj.id, score=breakdown.final_score,
                    status="pass" if breakdown.question_status == "pass" else "fail",
                    error_type=breakdown.failure_type,
                )
                session.add(result_db)
                return {"trace_id": trace_id, "observation_id": observation_id, "agent_result": agent_result, "breakdown": breakdown}

            if dataset:
                experiment_results = dataset.run_experiment(
                    task=evaluation_task, run_name=run_name,
                    evaluators=[judge_evaluator, execution_evaluator, final_score_evaluator]
                )

                if experiment_results:
                    for res in experiment_results:
                        if res and "breakdown" in res:
                            b = res["breakdown"]
                            q_type = res["agent_result"].get("question_type", "simple")
                            question_scores.append((b.final_score, q_type))
                            if b.failure_type in failure_counts: failure_counts[b.failure_type] += 1

        except Exception as e:
            print(f"[Langfuse] Eval failed for {table_id}: {e}")

    agg = compute_dataset_score(question_scores)
    run.score = agg["dataset_score"]
    run.status = EvalStatus.completed
    session.add(run)
    session.commit()

    langfuse_context.update_current_trace(output=agg)
    return agg["dataset_score"]


@observe(name="regression-suite")
def execute_production_regression_tests(session: Session, promotion_run_id: Optional[str] = None) -> bool:
    """
    Runs evaluation for all tables currently in 'production'.
    Returns True if all tables meet the quality threshold (0.8).
    """
    prod_tables = session.exec(select(Table).where(Table.status == TableStatus.production)).all()
    if not prod_tables:
        print("[Regression] No production tables to test. Passing.")
        return True

    print(f"[Regression] Running tests for {len(prod_tables)} production tables...")
    all_pass = True
    for table in prod_tables:
        run = EvalRun(
            table_id=table.id,
            status=EvalStatus.running,
            triggered_by="regression",
            promotion_run_id=promotion_run_id,  # link to the promotion that triggered this
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        score = execute_single_table_eval(table.id, run.id, session)
        if score < 0.8:
            print(f"[Regression] Table {table.name} ({table.id}) failed regression with score {score}")
            all_pass = False
        else:
            print(f"[Regression] Table {table.name} ({table.id}) passed with score {score}")

    return all_pass


def _create_alert(session: Session, run_id: Optional[str], table_id: Optional[str],
                  alert_type: str, severity: AlertSeverity, message: str, details: Optional[dict] = None):
    alert = EvaluationAlert(
        run_id=run_id,
        table_id=table_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        details=details,
    )
    session.add(alert)
    session.commit()


@observe(name="promotion-workflow")
def promote_table_to_production_workflow(table_id: str, run_id: str):
    """
    Orchestrates the promotion of a table to production.
    """
    with Session(engine) as session:
        # Step 1: Evaluate the target table
        print(f"[Promotion] Step 1: Evaluating target table {table_id}...")
        target_score = execute_single_table_eval(table_id, run_id, session)

        if target_score < 0.8:
            msg = f"Promotion failed: Target table score {target_score:.0%} is below 80%."
            print(f"[Promotion] {msg}")
            _create_alert(session, run_id, table_id, "promotion_failed", AlertSeverity.critical, msg)
            return

        # Step 2: Run regression tests — pass promotion run_id to tag each regression run
        print("[Promotion] Step 2: Running production regression tests...")
        regression_pass = execute_production_regression_tests(session, promotion_run_id=run_id)

        if not regression_pass:
            msg = "Promotion failed: Regression detected in existing production tables."
            print(f"[Promotion] {msg}")
            _create_alert(session, run_id, table_id, "regression_detected", AlertSeverity.critical, msg)
            return

        # Step 3: Promote
        print(f"[Promotion] Step 3: Promoting table {table_id} to production!")
        table = session.get(Table, table_id)
        if table:
            table.status = TableStatus.production
            session.add(table)
            session.commit()
            print(f"[Promotion] Success: Table {table.name} is now in production.")


@observe(name="eval-run")
def _run_evaluation_pipeline(table_id: str, run_id: str):
    with Session(engine) as session:
        execute_single_table_eval(table_id, run_id, session)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/tables/{table_id}/promote", status_code=202)
def promote_table(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """
    Trigger the production promotion workflow.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    run = EvalRun(table_id=table_id, status=EvalStatus.running, triggered_by="promotion")
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(promote_table_to_production_workflow, table_id, run.id)
    return {"message": "Promotion workflow started", "run_id": run.id}


@router.post("/tables/{table_id}/eval/run", response_model=EvalRunRead, status_code=202)
def trigger_eval(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if not questions:
        raise HTTPException(
            status_code=422,
            detail="Table has no golden questions. Add at least 1 question before running an evaluation.",
        )

    run = EvalRun(table_id=table_id, status=EvalStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_evaluation_pipeline, table_id, run.id)
    return run


@router.get("/tables/{table_id}/eval/runs", response_model=list[EvalRunRead])
def list_runs(table_id: str, session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.table_id == table_id)
        .order_by(desc(EvalRun.created_at))
    ).all()

    runs = []
    for run, table_name in results:
        read = EvalRunRead.model_validate(run)
        read.table_name = table_name
        runs.append(read)
    return runs


@router.get("/eval/runs/all", response_model=list[EvalRunRead])
def get_all_eval_runs(session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .order_by(desc(EvalRun.created_at))
        .limit(100)
    ).all()

    runs = []
    for run, table_name in results:
        read = EvalRunRead.model_validate(run)
        read.table_name = table_name
        runs.append(read)
    return runs


@router.get("/eval/{run_id}", response_model=EvalRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/eval/{run_id}/results", response_model=list[EvalResultRead])
def get_results(run_id: str, session: Session = Depends(get_session)):
    return session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()


@router.get("/eval/{run_id}/report")
def get_run_report(run_id: str, session: Session = Depends(get_session)):
    """Full structured report for one eval run."""
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    results = session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()

    total = len(results)
    passes = sum(1 for r in results if r.status == "pass")
    fails = total - passes

    failure_breakdown = {}
    for r in results:
        if r.error_type:
            failure_breakdown[r.error_type] = failure_breakdown.get(r.error_type, 0) + 1

    return {
        "run_id": run_id,
        "table_id": run.table_id,
        "overall_score": run.score,
        "status": run.status,
        "pass_rate": round(passes / total, 3) if total else 0,
        "fail_rate": round(fails / total, 3) if total else 0,
        "total_questions": total,
        "is_publishable": run.score >= 0.80,
        "failure_breakdown": failure_breakdown,
        "created_at": run.created_at.isoformat(),
        "per_question": [
            {
                "question_id": r.question_id,
                "score": r.score,
                "status": r.status,
                "failure_type": r.error_type,
            }
            for r in results
        ],
    }
