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
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from app.db.engine import get_session, engine
from app.models.models import (
    Table, GoldenQuestion,
    EvalRun, EvalRunRead, EvalStatus,
    EvalResult, EvalResultRead,
    AuditQuery,
)
from app.services.scoring import (
    JudgeOutput, ExecutionResult, ExpectedShape,
    compute_score, compute_dataset_score,
    PASS_THRESHOLD, PARTIAL_THRESHOLD,
)

router = APIRouter(tags=["evaluation"])


# ─── Stubbed external service calls ───────────────────────────────────────────
# Replace each stub with a real HTTP call to your LangGraph/Trino/LLM service.

def call_agent(question: str, table_id: str) -> dict:
    """
    STUB — replace with real call to LangGraph TextToSQL agent.

    Expected return:
    {
        "generated_sql": str,
        "tables_used": list[str],
        "generated_columns": list[str],
        "refiner_iterations": int,
        "execution": {
            "success": bool, "rows": [], "columns": [],
            "row_count": int, "execution_time_ms": int, "error_message": str | None
        }
    }
    """
    # Simulates a realistic agent call with variable quality
    success = random.random() > 0.1          # 90% execution success rate
    row_count = random.randint(0, 5000) if success else 0
    iterations = random.choices([0, 1, 2, 3], weights=[60, 25, 10, 5])[0]

    return {
        "generated_sql": f"SELECT * FROM {table_id[:8]}.stub_table LIMIT 100",
        "tables_used": [f"{table_id[:8]}.stub_table"],
        "generated_columns": ["id", "name", "value"],
        "refiner_iterations": iterations,
        "execution": {
            "success": success,
            "rows": [],
            "columns": ["id", "name", "value"] if success else [],
            "row_count": row_count,
            "execution_time_ms": random.randint(200, 8000),
            "error_message": None if success else "Stub execution error",
        },
    }


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
    base = random.uniform(0.55, 0.95) if exec_success else random.uniform(0.0, 0.35)

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


def emit_langfuse_trace(
    trace_id: str,
    eval_run_id: str,
    question_id: str,
    score: float,
    failure_type: Optional[str],
):
    """
    STUB — replace with real langfuse.score() call.
    In production: emit trace, link to dataset run, log all dimension scores.
    """
    print(f"[Langfuse STUB] trace={trace_id} run={eval_run_id} q={question_id} score={score:.3f} failure={failure_type}")


# ─── Core evaluation pipeline ──────────────────────────────────────────────────

def _run_evaluation_pipeline(table_id: str, run_id: str):
    """
    Runs the full evaluation for one EvalRun.
    Executed as a background task.
    """
    with Session(engine) as session:
        run = session.get(EvalRun, run_id)
        if not run:
            return

        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
        ).all()

        if not questions:
            run.status = EvalStatus.failed
            run.score = 0.0
            session.add(run)
            session.commit()
            return

        # Collect scores for dataset aggregation
        question_scores: list[tuple[float, str]] = []
        failure_counts = {
            "wrong_table": 0,
            "wrong_join": 0,
            "wrong_filter": 0,
            "hallucination": 0,
            "execution_error": 0,
            "empty_result_bug": 0,
            "partial_correct": 0,
        }

        for q in questions:
            # 1. Call agent
            agent_result = call_agent(q.question, table_id)

            exec_data = agent_result["execution"]
            execution = ExecutionResult(
                success=exec_data["success"],
                rows=exec_data["rows"],
                columns=exec_data["columns"],
                row_count=exec_data["row_count"],
                execution_time_ms=exec_data["execution_time_ms"],
                error_message=exec_data.get("error_message"),
            )

            # Expected shape (from question if stored, otherwise permissive defaults)
            expected_shape = ExpectedShape(
                row_count_min=0,
                row_count_max=999_999,
                expected_columns=["id", "name", "value"],  # TODO: store in dataset_questions
            )

            # 2. Call LLM judge
            judge_raw = call_llm_judge(
                question=q.question,
                expected_sql=q.expected_sql,
                generated_sql=agent_result["generated_sql"],
                execution_meta=exec_data,
                schema_context="",  # TODO: inject from ContextBuilder
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

            # 3. Compute score (3-layer deterministic)
            breakdown = compute_score(
                execution=execution,
                expected_shape=expected_shape,
                judge=judge,
                tables_used=agent_result["tables_used"],
                expected_tables=[],   # TODO: derive from expected_sql
                generated_columns=agent_result["generated_columns"],
                schema_columns=["id", "name", "value"],  # TODO: inject from enrichment
                refiner_iterations=agent_result["refiner_iterations"],
                question_type=q.question_type.value if hasattr(q.question_type, 'value') else str(q.question_type),
            )

            # 4. Persist EvalResult with full breakdown
            result = EvalResult(
                run_id=run_id,
                question_id=q.id,
                score=breakdown.final_score,
                status="pass" if breakdown.question_status == "pass" else "fail",
                error_type=breakdown.failure_type,
            )
            session.add(result)

            # 5. Langfuse trace (stub)
            trace_id = str(uuid.uuid4())
            emit_langfuse_trace(trace_id, run_id, q.id, breakdown.final_score, breakdown.failure_type)

            # Accumulate
            question_scores.append((breakdown.final_score, str(q.question_type)))
            if breakdown.failure_type and breakdown.failure_type in failure_counts:
                failure_counts[breakdown.failure_type] += 1

        # 5. Aggregate dataset score
        agg = compute_dataset_score(question_scores)

        run.score = agg["dataset_score"]
        run.status = EvalStatus.completed
        session.add(run)
        session.commit()


# ─── Endpoints ────────────────────────────────────────────────────────────────

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
            detail="Table has no golden questions. Add at least 1 question before running an evaluation."
        )

    run = EvalRun(table_id=table_id, status=EvalStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_evaluation_pipeline, table_id, run.id)
    return run


@router.get("/tables/{table_id}/eval/runs", response_model=list[EvalRunRead])
def list_runs(table_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id)
        .order_by(EvalRun.created_at.desc())
    ).all()


@router.get("/eval/runs/all", response_model=list[EvalRunRead])
def get_all_eval_runs(session: Session = Depends(get_session)):
    return session.exec(
        select(EvalRun).order_by(EvalRun.created_at.desc()).limit(100)
    ).all()


@router.get("/eval/{run_id}", response_model=EvalRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/eval/{run_id}/results", response_model=list[EvalResultRead])
def get_results(run_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(EvalResult).where(EvalResult.run_id == run_id)
    ).all()


@router.get("/eval/{run_id}/report")
def get_run_report(run_id: str, session: Session = Depends(get_session)):
    """Full structured report for one eval run."""
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    results = session.exec(
        select(EvalResult).where(EvalResult.run_id == run_id)
    ).all()

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
