"""
evaluation.py — Evaluation pipeline using TextToSQLEvaluator.

The core evaluation logic now lives in app/services/evaluator.py and mirrors
the BaseLangfuseEvaluator / TextToSQLEvaluator structure from the main
Text2SQL application — making the eventual app merge straightforward.

Merge checklist:
  1. Replace TextToSQLEvaluator._call_agent_stub() with the real MCP client call.
  2. Replace TextToSQLEvaluator._call_llm_judge_stub() with the real LLM judge.
  3. Wire TextToSQLEvaluator._execute_sql_query() to real Trino execution.
  4. Everything else (evaluators, dataset sync, aggregation) stays the same.
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
    EnrichmentVersion,
)
from app.services.scoring import compute_dataset_score
from app.services.langfuse_client import langfuse_client
from app.services.evaluator import TextToSQLEvaluator
from langfuse.decorators import observe, langfuse_context
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])


# ─── Core evaluation pipeline ──────────────────────────────────────────────────

@observe(name="eval-single-table")
def execute_single_table_eval(table_id: str, run_id: str, session: Session) -> float:
    """
    Core logic for evaluating a single table via TextToSQLEvaluator.
    Returns the final dataset score.
    """
    run = session.get(EvalRun, run_id)
    if not run:
        return -1.0

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()

    if not questions:
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    langfuse_context.update_current_trace(
        metadata={"table_id": table_id, "run_id": run_id},
        tags=["eval-run", f"table:{table_id}"]
    )

    # Shared accumulators — TextToSQLEvaluator.task() appends to these
    question_scores: list[tuple[float, str]] = []
    failure_counts = {
        "wrong_table": 0, "wrong_join": 0, "wrong_filter": 0,
        "hallucination": 0, "execution_error": 0, "empty_result_bug": 0, "partial_correct": 0,
    }
    dimension_totals = {
        "table_selection_correctness": 0.0,
        "sql_semantic_equivalence": 0.0,
        "result_correctness": 0.0
    }

    dataset_name = f"text2sql_{table_id[:8]}"
    run_name = f"EvalRun-{run_id}"

    if langfuse_client.enabled:
        try:
            table = session.get(Table, table_id)

            # Ensure dataset exists — create/sync only if missing
            if langfuse_client.dataset_exists(dataset_name):
                dataset = langfuse_client.get_dataset(dataset_name)
                logger.info(f"[Langfuse] Dataset '{dataset_name}' exists, skipping re-sync.")
            else:
                logger.info(f"[Langfuse] Dataset '{dataset_name}' missing, rebuilding...")
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
                logger.warning(f"[Langfuse] Dataset unavailable for {table_id}, scoring via stubs only.")
            else:
                # ── Delegate to TextToSQLEvaluator ─────────────────────────
                evaluator = TextToSQLEvaluator(
                    run_name=run_name,
                    session=session,
                    table_id=table_id,
                    run_id=run_id,
                    question_scores=question_scores,
                    failure_counts=failure_counts,
                    dimension_totals=dimension_totals
                )
                evaluator.run_single_dataset(dataset_name)

        except Exception as e:
            logger.error(f"[Langfuse] Eval failed for {table_id}: {e}")

    agg = compute_dataset_score(question_scores)
    n = len(question_scores)
    
    run.score = agg.get("dataset_score", 0.0)
    run.pass_rate = agg.get("pass_rate", 0.0)
    run.fail_rate = agg.get("fail_rate", 0.0)
    run.total_questions = n
    run.failure_breakdown = failure_counts
    run.dimension_averages = {k: round(v / n, 3) for k, v in dimension_totals.items()} if n > 0 else {}
    run.status = EvalStatus.completed
    run.completed_at = datetime.utcnow()
    
    session.add(run)
    
    # Update table status lifecycle
    table = session.get(Table, table_id)
    if table:
        if table.status == TableStatus.draft:
            table.status = TableStatus.sandbox
        elif table.status in [TableStatus.verified, TableStatus.production]:
            # If a manual run fails PASS_THRESHOLD on a production/verified table, demote it
            if run.score < 0.85:
                table.status = TableStatus.degraded
        session.add(table)
        
    session.commit()
    
    logger.info(f"[Evaluation] Finished table {table_id} with score {run.score} "
                f"({run.total_questions} questions)")

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
        logger.info("[Regression] No production tables to test. Passing.")
        return True

    logger.info(f"[Regression] Running tests for {len(prod_tables)} production tables...")
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
            logger.warning(f"[Regression] Table {table.name} ({table.id}) "
                          f"failed regression with score {score} that is less than 80%")
            all_pass = False
        else:
            logger.info(f"[Regression] Table {table.name} ({table.id}) "  
                        f"passed with score {score} that is greater than or equal to 80%")

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
        logger.info(f"[Promotion] Step 1: Evaluating target table {table_id}...")
        target_score = execute_single_table_eval(table_id, run_id, session)

        if target_score < 0.8:
            msg = f"Promotion failed: Target table score {target_score:.0%} is below 80%."
            logger.error(f"[Promotion] {msg}")
            _create_alert(session, run_id, table_id, "promotion_failed", AlertSeverity.critical, msg)
            return

        # Step 2: Run regression tests — pass promotion run_id to tag each regression run
        logger.info("[Promotion] Step 2: Running production regression tests...")
        regression_pass = execute_production_regression_tests(session, promotion_run_id=run_id)

        if not regression_pass:
            msg = "Promotion failed: Regression detected in existing production tables."
            logger.error(f"[Promotion] {msg}")
            _create_alert(session, run_id, table_id, "regression_detected", AlertSeverity.critical, msg)
            return

        # Step 3: Promote to verified (awaiting admin approval)
        logger.info(f"[Promotion] Step 3: Promoting table {table_id} to verified!")
        table = session.get(Table, table_id)
        if table:
            table.status = TableStatus.verified
            session.add(table)
            session.commit()
            logger.info(f"[Promotion] Success: Table {table.name} is now verified and awaiting admin approval.")


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


@router.get("/eval/readiness")
def get_readiness(session: Session = Depends(get_session)):
    """
    Return readiness status for every table.
    Response: { tableId: { ready: bool, missing: [str] } }
    """
    tables = session.exec(select(Table)).all()
    result = {}
    for table in tables:
        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table.id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()

        missing: list[str] = []
        if not enrichment or not enrichment.data:
            missing.append("table enrichment / schema description")
        elif not enrichment.data.get("table_description"):
            missing.append("table description")

        q_count = len(session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all())
        if q_count == 0:
            missing.append("golden questions")

        result[table.id] = {"ready": len(missing) == 0, "missing": missing}
    return result


@router.post("/tables/{table_id}/eval/run", response_model=EvalRunRead, status_code=202)
def trigger_eval(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    # Validate enrichment — table must have a description before it can be evaluated
    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    missing: list[str] = []
    if not enrichment or not enrichment.data:
        missing.append("table enrichment / schema description")
    elif not enrichment.data.get("table_description"):
        missing.append("table description (enrichment exists but has no description)")

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if not questions:
        missing.append("golden questions (at least 1 required)")

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot run evaluation. Missing required data: {'; '.join(missing)}.",
        )

    run = EvalRun(table_id=table_id, status=EvalStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_evaluation_pipeline, table_id, run.id)
    
    # Return EvalRunRead with table_name populated
    read = EvalRunRead.model_validate(run, update={"table_name": table.name})
    return read


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
        read = EvalRunRead.model_validate(run, update={"table_name": table_name})
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
        read = EvalRunRead.model_validate(run, update={"table_name": table_name})
        runs.append(read)
    return runs


@router.get("/eval/batch/{promotion_run_id}", response_model=list[EvalRunRead])
def get_batch_runs(promotion_run_id: str, session: Session = Depends(get_session)):
    """Get all runs linked to a promotion batch (the promotion run itself + regression tests)."""
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where((EvalRun.id == promotion_run_id) | (EvalRun.promotion_run_id == promotion_run_id))
        .order_by(desc(EvalRun.created_at))
    ).all()

    runs = []
    for run, table_name in results:
        read = EvalRunRead.model_validate(run, update={"table_name": table_name or "Unknown Table"})
        runs.append(read)
    return runs


@router.get("/eval/{run_id}", response_model=EvalRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    result = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.id == run_id)
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Eval run not found")
        
    run, table_name = result
    return EvalRunRead.model_validate(run, update={"table_name": table_name})


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
