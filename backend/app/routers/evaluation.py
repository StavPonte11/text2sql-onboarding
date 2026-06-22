"""
evaluation.py — Evaluation pipeline with two-phase promotion workflow.

Promotion flow:
  Phase A: Measure baseline contains_execution_accuracy on all production tables
           using the single shared 'text2sql_production' Langfuse dataset.

Pass criteria (both must be met):
  1. candidate_score  >= 0.50
  2. regression_score >= baseline_score - 0.10

On pass  → table.status = verified  (awaits admin approval)
On fail  → table.status = sandbox   + alert created
"""

import logging
from datetime import UTC, datetime
from typing import Literal

import requests
from core.db.engine import engine, get_session
from core.models.models import (
    AlertSeverity,
    EnrichmentVersion,
    EvalResult,
    EvalResultRead,
    EvalRun,
    EvalRunRead,
    EvalStatus,
    EvaluationAlert,
    GoldenQuestion,
    Table,
    TableStatus,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langfuse.decorators import langfuse_context, observe
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from app.config import settings
from app.services.langfuse_client import langfuse_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])


class EvalAPIRequest(BaseModel):
    tables_names: list[str]
    dataset_name: str


class EvalAPIQuestionMetrics(BaseModel):
    exact_match: float
    exact_execution_accuracy: float
    contains_execution_accuracy: float


class EvalAPIQuestionResult(BaseModel):
    question_id: str
    generated_sql: str | None = None
    metrics: EvalAPIQuestionMetrics
    status: Literal["pass", "fail"]
    error_message: str | None = None
    row_count: int | None = None


class EvalAPIOverallMetrics(BaseModel):
    contains_execution_accuracy: float
    exact_execution_accuracy: float
    exact_match: float
    total_questions: int
    pass_rate: float
    fail_rate: float


class EvalAPIResponse(BaseModel):
    run_id: str
    status: Literal["completed", "failed"]
    overall_metrics: EvalAPIOverallMetrics
    results: list[EvalAPIQuestionResult]


# Name of the single shared Langfuse dataset for all production table questions
PRODUCTION_DATASET_NAME = "text2sql_production"


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _create_alert(
    session: Session,
    run_id: str | None,
    table_id: str | None,
    alert_type: str,
    severity: AlertSeverity,
    message: str,
    details: dict | None = None,
):
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


def _build_questions_payload(questions: list, table: Table) -> list:
    return [
        {
            "question_id": q.id,
            "question_text": q.question,
            "expected_sql": q.expected_sql or "",
            "table_id": q.table_id,
            "schema_name": table.schema_name,
            "question_type": q.question_type.value
            if hasattr(q.question_type, "value")
            else str(q.question_type),
            "difficulty": q.difficulty.value
            if hasattr(q.difficulty, "value")
            else str(q.difficulty),
        }
        for q in questions
    ]


# ─── Core evaluation runner (single dataset) ───────────────────────────────────


@observe(name="eval-single-table")
def execute_single_table_eval(table_id: str, run_id: str, session: Session) -> float:
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
        tags=["eval-run", f"table:{table_id}"],
    )

    table = session.get(Table, table_id)
    dataset_name = f"text2sql_sandbox_{table_id}"

    if langfuse_client.enabled:
        langfuse_client.ensure_dataset_synced(
            dataset_name, _build_questions_payload(questions, table)
        )

    try:
        req = EvalAPIRequest(tables_names=[table.name], dataset_name=dataset_name)
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req.model_dump(),
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = EvalAPIResponse(**resp.json())
        if eval_resp.status == "failed":
            raise Exception("API returned failed status")
    except Exception as e:
        logger.error(f"[Eval] Table {table_id} evaluation failed via API: {e}")
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    metrics = eval_resp.overall_metrics
    run.score = metrics.contains_execution_accuracy
    run.pass_rate = metrics.pass_rate
    run.fail_rate = metrics.fail_rate
    run.total_questions = metrics.total_questions
    run.status = EvalStatus.completed
    run.completed_at = datetime.now(UTC)
    run.dimension_averages = {
        "contains_execution_accuracy": metrics.contains_execution_accuracy,
        "exact_execution_accuracy": metrics.exact_execution_accuracy,
        "exact_match": metrics.exact_match,
    }
    session.add(run)

    for q_res in eval_resp.results:
        session.add(
            EvalResult(
                run_id=run_id,
                question_id=q_res.question_id,
                score=q_res.metrics.contains_execution_accuracy,
                status=q_res.status,
            )
        )

    # Lifecycle: draft → sandbox on first evaluation only
    if table and table.status == TableStatus.draft:
        table.status = TableStatus.sandbox
        session.add(table)

    session.commit()

    logger.info(
        f"[Eval] Table {table_id}: contains_exec_accuracy={metrics.contains_execution_accuracy} "
        f"exact_exec_accuracy={metrics.exact_execution_accuracy} exact_match={metrics.exact_match} "
        f"({metrics.total_questions} questions, pass_rate={metrics.pass_rate})"
    )
    langfuse_context.update_current_trace(
        output={
            "score": metrics.contains_execution_accuracy,
            "pass_rate": metrics.pass_rate,
        }
    )
    return metrics.contains_execution_accuracy


# ─── Phase A: measure baseline score on production dataset ────────────────────


def _run_production_dataset_eval(
    session: Session, run_name_prefix: str, promotion_run_id: str
) -> float:
    prod_tables = session.exec(
        select(Table).where(Table.status == TableStatus.production)
    ).all()

    if not prod_tables:
        logger.info("[Promotion/Phase-A] No production tables — baseline score = 1.0")
        return 1.0

    all_production_questions: list[GoldenQuestion] = []
    for table in prod_tables:
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        all_production_questions.extend(qs)

    if langfuse_client.enabled:
        all_questions_payload = []
        for table in prod_tables:
            qs_for_table = [
                q for q in all_production_questions if q.table_id == table.id
            ]
            all_questions_payload.extend(_build_questions_payload(qs_for_table, table))

        if all_questions_payload:
            try:
                langfuse_client.sync_dataset(
                    PRODUCTION_DATASET_NAME, all_questions_payload
                )
            except Exception as e:
                logger.warning(f"[Promotion/Phase-A] Dataset sync failed: {e}")

    run = EvalRun(
        table_id=None,
        status=EvalStatus.running,
        triggered_by="promotion-baseline",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    table_names = [t.name for t in prod_tables]
    try:
        req = EvalAPIRequest(
            tables_names=table_names, dataset_name=PRODUCTION_DATASET_NAME
        )
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req.model_dump(),
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = EvalAPIResponse(**resp.json())
        if eval_resp.status == "failed":
            raise Exception("API returned failed status")
    except Exception as e:
        logger.error(f"[Promotion/Phase-A] Baseline eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    metrics = eval_resp.overall_metrics
    run.score = metrics.contains_execution_accuracy
    run.pass_rate = metrics.pass_rate
    run.fail_rate = metrics.fail_rate
    run.total_questions = metrics.total_questions
    run.status = EvalStatus.completed
    run.completed_at = datetime.now(UTC)
    run.dimension_averages = {
        "contains_execution_accuracy": metrics.contains_execution_accuracy,
        "exact_execution_accuracy": metrics.exact_execution_accuracy,
        "exact_match": metrics.exact_match,
    }
    session.add(run)

    for q_res in eval_resp.results:
        session.add(
            EvalResult(
                run_id=run.id,
                question_id=q_res.question_id,
                score=q_res.metrics.contains_execution_accuracy,
                status=q_res.status,
            )
        )
    session.commit()

    logger.info(
        f"[Promotion/Phase-A] Baseline contains_exec_accuracy = {metrics.contains_execution_accuracy:.3f} "
        f"exact_exec_accuracy = {metrics.exact_execution_accuracy:.3f} exact_match = {metrics.exact_match:.3f} "
        f"({metrics.total_questions} questions)"
    )
    return metrics.contains_execution_accuracy


# We no longer use a fixed dataset name to avoid soft-delete conflicts and question accumulation.
# Each promotion run gets a unique candidate dataset.


def _run_candidate_eval(
    table: Table,
    questions: list,
    run_name_prefix: str,
    session: Session,
    promotion_run_id: str,
) -> float:
    run = EvalRun(
        table_id=table.id,
        status=EvalStatus.running,
        triggered_by="promotion-candidate",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    dataset_name = "text2sql_candidate"

    if langfuse_client.enabled:
        try:
            langfuse_client.ensure_dataset_synced(
                dataset_name, _build_questions_payload(questions, table)
            )
        except Exception as e:
            logger.error(f"[Promotion/Phase-B] Candidate eval prep failed: {e}")

    try:
        req = EvalAPIRequest(tables_names=[table.name], dataset_name=dataset_name)
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req.model_dump(),
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = EvalAPIResponse(**resp.json())
        if eval_resp.status == "failed":
            raise Exception("API returned failed status")
    except Exception as e:
        logger.error(f"[Promotion/Phase-B] Candidate eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    metrics = eval_resp.overall_metrics
    run.score = metrics.contains_execution_accuracy
    run.pass_rate = metrics.pass_rate
    run.fail_rate = metrics.fail_rate
    run.total_questions = metrics.total_questions
    run.status = EvalStatus.completed
    run.completed_at = datetime.now(UTC)
    run.dimension_averages = {
        "contains_execution_accuracy": metrics.contains_execution_accuracy,
        "exact_execution_accuracy": metrics.exact_execution_accuracy,
        "exact_match": metrics.exact_match,
    }
    session.add(run)

    for q_res in eval_resp.results:
        session.add(
            EvalResult(
                run_id=run.id,
                question_id=q_res.question_id,
                score=q_res.metrics.contains_execution_accuracy,
                status=q_res.status,
            )
        )
    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Candidate '{table.name}' contains_score = {metrics.contains_execution_accuracy:.3f} "
        f"exact_score = {metrics.exact_execution_accuracy:.3f} exact_match = {metrics.exact_match:.3f}"
    )

    if langfuse_client.enabled:
        # Since API might be async or Langfuse is async, we may still need to clear dataset
        # Here we don't wait for traces, just clear it after evaluation finishes
        langfuse_client.clear_dataset(dataset_name)

    return metrics.contains_execution_accuracy


def _run_regression_eval(
    run_name_prefix: str, session: Session, promotion_run_id: str
) -> float:
    prod_tables = session.exec(
        select(Table).where(Table.status == TableStatus.production)
    ).all()
    all_production_questions: list[GoldenQuestion] = []
    table_names = []
    for table in prod_tables:
        table_names.append(table.name)
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        all_production_questions.extend(qs)

    run = EvalRun(
        table_id=None,
        status=EvalStatus.running,
        triggered_by="promotion-regression",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        req = EvalAPIRequest(
            tables_names=table_names, dataset_name=PRODUCTION_DATASET_NAME
        )
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req.model_dump(),
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = EvalAPIResponse(**resp.json())
        if eval_resp.status == "failed":
            raise Exception("API returned failed status")
    except Exception as e:
        logger.error(f"[Promotion/Phase-B] Regression eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    metrics = eval_resp.overall_metrics
    run.score = metrics.contains_execution_accuracy
    run.pass_rate = metrics.pass_rate
    run.fail_rate = metrics.fail_rate
    run.total_questions = metrics.total_questions
    run.status = EvalStatus.completed
    run.completed_at = datetime.now(UTC)
    run.dimension_averages = {
        "contains_execution_accuracy": metrics.contains_execution_accuracy,
        "exact_execution_accuracy": metrics.exact_execution_accuracy,
        "exact_match": metrics.exact_match,
    }
    session.add(run)

    for q_res in eval_resp.results:
        session.add(
            EvalResult(
                run_id=run.id,
                question_id=q_res.question_id,
                score=q_res.metrics.contains_execution_accuracy,
                status=q_res.status,
            )
        )
    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Regression contains_score (with candidate) = {metrics.contains_execution_accuracy:.3f} "
        f"exact_score = {metrics.exact_execution_accuracy:.3f} exact_match = {metrics.exact_match:.3f}"
    )
    return metrics.contains_execution_accuracy


# ─── Main promotion workflow ───────────────────────────────────────────────────


@observe(name="promotion-workflow")
def promote_table_to_production_workflow(table_id: str, run_id: str):
    """
    Two-phase promotion workflow.

    Phase A — Baseline:
      1. Collect all production tables' questions into text2sql_production dataset.
      2. Run evaluation → baseline_score (contains_execution_accuracy).

    Phase B — Candidate:
      1. Run eval on temp dataset of candidate's questions → candidate_score.
      2. Re-run production dataset eval → regression_score.

    Pass criteria:
      candidate_score  >= 0.50
      regression_score >= baseline_score - 0.10

    Outcome:
      PASS → table.status = verified (admin approval required)
      FAIL → table.status = sandbox  + alert
    """
    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            logger.error(f"[Promotion] Table not found: table_id={table_id}")
            return

        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
        ).all()

        if not questions:
            msg = f"Promotion failed: table '{table.name}' has no golden questions."
            logger.error(f"[Promotion] {msg}")
            _create_alert(
                session,
                run_id,
                table_id,
                "promotion_failed",
                AlertSeverity.critical,
                msg,
            )
            return

        run_name_prefix = f"Promo-{run_id[:8]}"

        # ── Phase A: baseline ──────────────────────────────────────────────────
        logger.info(f"[Promotion] Phase A: measuring baseline score for run {run_id}")
        baseline_score = _run_production_dataset_eval(
            session, run_name_prefix, promotion_run_id=run_id
        )
        threshold_min = round(baseline_score - 0.10, 3)
        logger.info(
            f"[Promotion] Baseline={baseline_score:.3f}, regression must be >= {threshold_min:.3f}"
        )

        candidate_score = 0.0
        regression_score = 0.0

        # ── Phase B step 1: evaluate candidate's golden questions ──────────
        candidate_score = _run_candidate_eval(
            table, questions, run_name_prefix, session, promotion_run_id=run_id
        )

        # ── Phase B step 2: regression on production dataset ───────────────
        if candidate_score >= 0.50:
            regression_score = _run_regression_eval(
                run_name_prefix, session, promotion_run_id=run_id
            )
        else:
            regression_score = baseline_score  # Skip regression, candidate failed

        # ── Evaluate pass criteria ─────────────────────────────────────────────
        candidate_ok = candidate_score >= 0.50
        regression_ok = regression_score >= threshold_min and regression_score >= 0.50

        if candidate_ok and regression_ok:
            logger.info(
                f"[Promotion] PASS — candidate={candidate_score:.3f} (≥0.50), "
                f"regression={regression_score:.3f} (≥{threshold_min:.3f})"
            )
            table.status = TableStatus.verified
            session.add(table)
        else:
            reasons = []
            if not candidate_ok:
                reasons.append(f"candidate score {candidate_score:.0%} < 50%")
            if not regression_ok:
                reasons.append(
                    f"regression score {regression_score:.0%} dropped more than 10% "
                    f"below baseline {baseline_score:.0%}"
                )
            msg = "Promotion failed: " + "; ".join(reasons)
            logger.error(f"[Promotion] FAIL — {msg}")
            table.status = TableStatus.sandbox
            session.add(table)
            _create_alert(
                session,
                run_id,
                table_id,
                "promotion_failed",
                AlertSeverity.critical,
                msg,
                {
                    "baseline_score": baseline_score,
                    "candidate_score": candidate_score,
                    "regression_score": regression_score,
                },
            )

        session.commit()
        logger.info(f"[Promotion] Done. Table '{table.name}' → {table.status}")


@observe(name="eval-run")
def _run_evaluation_pipeline(table_id: str, run_id: str):
    with Session(engine) as session:
        execute_single_table_eval(table_id, run_id, session)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/eval/readiness")
def get_readiness(session: Session = Depends(get_session)):
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

        q_count = len(
            session.exec(
                select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
            ).all()
        )
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

    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    missing: list[str] = []
    if not enrichment or not enrichment.data:
        missing.append("table enrichment / schema description")
    elif not enrichment.data.get("table_description"):
        missing.append("table description")

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if not questions:
        missing.append("golden questions (at least 1 required)")

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot run evaluation. Missing: {'; '.join(missing)}.",
        )

    run = EvalRun(table_id=table_id, status=EvalStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_evaluation_pipeline, table_id, run.id)
    return EvalRunRead.model_validate(run, update={"table_name": table.name})


@router.get("/tables/{table_id}/eval/runs", response_model=list[EvalRunRead])
def list_runs(table_id: str, session: Session = Depends(get_session)):
    # Subquery to find all promotion_run_ids for this table
    promotion_run_ids = select(EvalRun.id).where(EvalRun.table_id == table_id)

    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where(
            (
                (EvalRun.table_id == table_id)
                | (EvalRun.promotion_run_id.in_(promotion_run_ids))
            )
            & (EvalRun.triggered_by != "promotion")
        )
        .order_by(desc(EvalRun.created_at))
    ).all()

    out = []
    for run, name in results:
        # Provide a descriptive name for baseline/regression runs that have no table_id
        if not name:
            if run.triggered_by == "promotion-baseline":
                name = "Production Baseline"
            elif run.triggered_by == "promotion-regression":
                name = "Production Regression"
            else:
                name = "Unknown"
        out.append(EvalRunRead.model_validate(run, update={"table_name": name}))
    return out


@router.get("/eval/runs/all", response_model=list[EvalRunRead])
def get_all_eval_runs(session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.triggered_by != "promotion")
        .order_by(desc(EvalRun.created_at))
        .limit(100)
    ).all()
    return [
        EvalRunRead.model_validate(run, update={"table_name": name})
        for run, name in results
    ]


@router.get("/eval/batch/{promotion_run_id}", response_model=list[EvalRunRead])
def get_batch_runs(promotion_run_id: str, session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where(
            (
                (EvalRun.id == promotion_run_id)
                | (EvalRun.promotion_run_id == promotion_run_id)
            )
            & (EvalRun.triggered_by != "promotion")
        )
        .order_by(desc(EvalRun.created_at))
    ).all()
    return [
        EvalRunRead.model_validate(run, update={"table_name": name or "Unknown"})
        for run, name in results
    ]


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
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    results = session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()

    total = len(results)
    passes = sum(1 for r in results if r.status == "pass")

    return {
        "run_id": run_id,
        "table_id": run.table_id,
        "contains_execution_accuracy": run.score,
        "pass_rate": round(passes / total, 3) if total else 0,
        "total_questions": total,
        "is_publishable": run.score >= 0.50,
        "regression_detected": run.regression_detected,
        "regression_delta": run.regression_delta,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "per_question": [
            {"question_id": r.question_id, "score": r.score, "status": r.status}
            for r in results
        ],
    }


@router.get("/eval/{run_id}/regression-diff")
def get_regression_diff(run_id: str, session: Session = Depends(get_session)):
    """
    For a promotion-regression run, return questions that passed the baseline
    but failed in the regression — true regressions introduced by the new candidate table.
    """
    regression_run = session.get(EvalRun, run_id)
    if not regression_run:
        raise HTTPException(status_code=404, detail="Run not found")
    if regression_run.triggered_by != "promotion-regression":
        raise HTTPException(
            status_code=400, detail="Run is not a promotion-regression run"
        )

    # Find the baseline run for the same promotion workflow
    baseline_run = session.exec(
        select(EvalRun).where(
            EvalRun.promotion_run_id == regression_run.promotion_run_id,
            EvalRun.triggered_by == "promotion-baseline",
        )
    ).first()

    if not baseline_run:
        return {
            "baseline_run_id": None,
            "regression_run_id": run_id,
            "total_regressions": 0,
            "regressions": [],
        }

    # Load EvalResult rows for both runs, keyed by question_id
    baseline_results = {
        r.question_id: r
        for r in session.exec(
            select(EvalResult).where(EvalResult.run_id == baseline_run.id)
        ).all()
    }
    regression_results = {
        r.question_id: r
        for r in session.exec(
            select(EvalResult).where(EvalResult.run_id == run_id)
        ).all()
    }

    # Questions that passed baseline but failed regression = true regressions
    regressed_ids = [
        qid
        for qid, br in baseline_results.items()
        if br.status == "pass"
        and qid in regression_results
        and regression_results[qid].status == "fail"
    ]

    # Fetch question text for display
    questions_map = {
        q.id: q
        for q in session.exec(
            select(GoldenQuestion).where(GoldenQuestion.id.in_(regressed_ids))
        ).all()
    }

    regressions = sorted(
        [
            {
                "question_id": qid,
                "question": questions_map[qid].question
                if qid in questions_map
                else "Unknown",
                "baseline_score": round(baseline_results[qid].score, 3),
                "regression_score": round(regression_results[qid].score, 3),
                "score_drop": round(
                    baseline_results[qid].score - regression_results[qid].score, 3
                ),
            }
            for qid in regressed_ids
        ],
        key=lambda x: x["score_drop"],  # type: ignore[return-value]
        reverse=True,
    )

    return {
        "baseline_run_id": baseline_run.id,
        "regression_run_id": run_id,
        "total_regressions": len(regressions),
        "regressions": regressions,
    }
