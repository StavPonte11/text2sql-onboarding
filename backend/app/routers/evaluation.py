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
from datetime import datetime

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
    EvaluationHistoryMetric,
    GoldenQuestion,
    Table,
    TableStatus,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langfuse import observe, propagate_attributes
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from app.config import settings
from app.services.langfuse_client import langfuse_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])


class LatencyStatsDTO(BaseModel):
    p50: float
    p95: float
    p99: float
    average: float
    minimum: float
    maximum: float
    total_samples: int


class AccuracyStatsDTO(BaseModel):
    execution_accuracy: float
    contains_accuracy: float
    sql_exact_match: float
    time_shift_score: float
    component_match: float
    schema_hallucination: float
    dialect_error: float
    composite_score: float


class FailureCategoryDTO(BaseModel):
    category: str
    count: int
    rate: float


class FailureAnalysisDTO(BaseModel):
    total_failures: int
    failure_rate: float
    agent_crash_count: int
    agent_crash_rate: float
    sql_execution_failure_count: int
    sql_execution_failure_rate: float
    trino_failure_count: int
    trino_failure_rate: float
    timeout_count: int
    timeout_rate: float
    validation_failure_count: int
    validation_failure_rate: float
    categories: list[FailureCategoryDTO]


class PerformanceStatsDTO(BaseModel):
    average_total_execution_time_ms: float
    average_time_to_first_row_ms: float
    total_token_usage: int
    average_token_usage: float
    average_refiner_iterations: float


class RunDatasetCaseResultDTO(BaseModel):
    question_id: str
    generated_sql: str | None = None
    expected_sql: str | None = None
    succeeded: bool
    error: str | None = None
    scores: dict[str, float]


class RunDatasetResponse(BaseModel):
    dataset_name: str
    run_id: str
    total_cases: int
    passed: int
    failed: int
    failure_rate: float
    latency: LatencyStatsDTO
    accuracy: AccuracyStatsDTO
    failure_analysis: FailureAnalysisDTO
    performance: PerformanceStatsDTO
    langfuse_trace_id: str | None = None
    duration_seconds: float
    cases: list[RunDatasetCaseResultDTO]


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
            "catalog_name": table.catalog,
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


def _map_and_save_run_metrics(
    run: EvalRun, eval_resp: RunDatasetResponse, session: Session, run_id: str
):
    run.score = eval_resp.accuracy.contains_accuracy
    # Guard: if no questions were evaluated, pass_rate is meaningless — use 0.0
    if eval_resp.total_cases == 0:
        run.pass_rate = 0.0
        run.fail_rate = 1.0
    else:
        run.pass_rate = 1.0 - eval_resp.failure_rate
        run.fail_rate = eval_resp.failure_rate
    if eval_resp.total_cases > 0 or not run.total_questions:
        run.total_questions = eval_resp.total_cases
    run.duration_seconds = eval_resp.duration_seconds
    run.status = EvalStatus.completed
    run.completed_at = datetime.now()

    # Store failure breakdown details
    failure_breakdown = {
        c.category: c.count for c in eval_resp.failure_analysis.categories
    }
    failure_breakdown.update(
        {
            "agent_crash": eval_resp.failure_analysis.agent_crash_count,
            "sql_execution_failure": eval_resp.failure_analysis.sql_execution_failure_count,
            "trino_failure": eval_resp.failure_analysis.trino_failure_count,
            "timeout": eval_resp.failure_analysis.timeout_count,
            "validation_failure": eval_resp.failure_analysis.validation_failure_count,
        }
    )
    run.failure_breakdown = failure_breakdown

    run.dimension_averages = {
        "contains_execution_accuracy": eval_resp.accuracy.contains_accuracy,
        "exact_execution_accuracy": eval_resp.accuracy.execution_accuracy,
        "exact_match": eval_resp.accuracy.sql_exact_match,
        "time_shift_score": eval_resp.accuracy.time_shift_score,
        "component_match": eval_resp.accuracy.component_match,
        "schema_hallucination": eval_resp.accuracy.schema_hallucination,
        "dialect_error": eval_resp.accuracy.dialect_error,
    }
    session.add(run)

    # Only insert EvalResult rows for question_ids that are valid FK references
    # (i.e. exist in golden_questions). External benchmark cases (e.g. Spider2) won't match.
    if eval_resp.cases:
        case_ids = [c.question_id for c in eval_resp.cases]
        valid_ids = set(
            session.exec(
                select(GoldenQuestion.id).where(GoldenQuestion.id.in_(case_ids))
            ).all()
        )
        for case in eval_resp.cases:
            if case.question_id in valid_ids:
                score = case.scores.get("contains_accuracy", 0.0)
                status = "pass" if score >= 0.5 else "fail"
                error_type = None if status == "pass" else case.error
                session.add(
                    EvalResult(
                        run_id=run_id,
                        question_id=case.question_id,
                        score=score,
                        status=status,
                        error_type=error_type,
                    )
                )


@observe(name="eval-single-table")
def execute_single_table_eval(table_id: str, run_id: str, session: Session) -> float:
    with propagate_attributes(
        metadata={"table_id": table_id, "run_id": run_id},
        tags=["eval-run", f"table:{table_id}"],
    ):
        run = session.get(EvalRun, run_id)
        if not run:
            return -1.0

        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
        ).all()

        if not questions:
            run.status = EvalStatus.failed
            run.score = 0.0
            run.total_questions = 0
            session.add(run)
            session.commit()
            return 0.0

        if run.total_questions == 0:
            run.total_questions = len(questions)
            session.add(run)
            session.commit()

        table = session.get(Table, table_id)
        dataset_name = f"text2sql_sandbox_{table_id}"

        if langfuse_client.enabled:
            try:
                langfuse_client.ensure_dataset_synced(
                    dataset_name, _build_questions_payload(questions, table)
                )
            except Exception as e:
                logger.warning(
                    f"[Eval] Langfuse dataset sync failed (eval will continue): {e}"
                )

        try:
            req = {
                "dataset_name": dataset_name,
                "additional_tables": [
                    f"{table.catalog}.{table.schema_name}.{table.name}"
                ],
            }
            resp = requests.post(
                f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
                json=req,
                timeout=600,
            )
            resp.raise_for_status()
            eval_resp = RunDatasetResponse(**resp.json())
        except Exception as e:
            logger.error(f"[Eval] Table {table_id} evaluation failed via API: {e}")
            run.status = EvalStatus.failed
            run.score = 0.0
            if questions:
                run.total_questions = len(questions)
            session.add(run)
            session.commit()
            return 0.0

        _map_and_save_run_metrics(run, eval_resp, session, run_id)

        # Lifecycle: draft → sandbox on first evaluation only
        if table and table.status == TableStatus.draft:
            table.status = TableStatus.sandbox
            session.add(table)

        session.commit()

        logger.info(
            f"[Eval] Table {table_id}: contains_accuracy={eval_resp.accuracy.contains_accuracy} "
            f"exec_accuracy={eval_resp.accuracy.execution_accuracy} exact_match={eval_resp.accuracy.sql_exact_match} "
            f"({eval_resp.total_cases} questions, pass_rate={1.0 - eval_resp.failure_rate})"
        )
        if langfuse_client.client and langfuse_client.client.get_current_trace_id():
            langfuse_client.client.set_current_trace_io(
                output={
                    "score": eval_resp.accuracy.contains_accuracy,
                    "pass_rate": 1.0 - eval_resp.failure_rate,
                },
            )
        return eval_resp.accuracy.contains_accuracy


# ─── Phase A: measure baseline score on production dataset ────────────────────


def _run_production_dataset_eval(
    session: Session, run_name_prefix: str, promotion_run_id: str
) -> float:
    prod_tables = session.exec(
        select(Table)
        .where(Table.status == TableStatus.production)
        .where(Table.owner_id != "spider2")
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
        total_questions=len(all_production_questions),
        status=EvalStatus.running,
        triggered_by="promotion-baseline",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Send schema.table format so the agent can validate tables without ambiguity
    table_names = [
        f"{t.schema_name}.{t.name}" if t.schema_name else t.name for t in prod_tables
    ]
    try:
        req = {
            "dataset_name": PRODUCTION_DATASET_NAME,
            "additional_tables": table_names,
        }
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req,
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = RunDatasetResponse(**resp.json())
    except Exception as e:
        logger.error(f"[Promotion/Phase-A] Baseline eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = 0.0
        run.total_questions = len(all_production_questions)
        session.add(run)
        session.commit()
        return 0.0

    _map_and_save_run_metrics(run, eval_resp, session, run.id)
    session.commit()

    logger.info(
        f"[Promotion/Phase-A] Baseline contains_exec_accuracy = {eval_resp.accuracy.contains_accuracy:.3f} "
        f"exact_exec_accuracy = {eval_resp.accuracy.execution_accuracy:.3f} exact_match = {eval_resp.accuracy.sql_exact_match:.3f} "
        f"({eval_resp.total_cases} questions)"
    )
    return eval_resp.accuracy.contains_accuracy


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
        total_questions=len(questions),
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
        req = {
            "dataset_name": dataset_name,
            "additional_tables": [f"{table.catalog}.{table.schema_name}.{table.name}"],
        }
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req,
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = RunDatasetResponse(**resp.json())
    except Exception as e:
        logger.error(f"[Promotion/Phase-B] Candidate eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = 0.0
        run.total_questions = len(questions)
        session.add(run)
        session.commit()
        return 0.0

    _map_and_save_run_metrics(run, eval_resp, session, run.id)
    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Candidate '{table.name}' contains_score = {eval_resp.accuracy.contains_accuracy:.3f} "
        f"exact_score = {eval_resp.accuracy.execution_accuracy:.3f} exact_match = {eval_resp.accuracy.sql_exact_match:.3f}"
    )

    if langfuse_client.enabled:
        langfuse_client.clear_dataset(dataset_name)

    return eval_resp.accuracy.contains_accuracy


def _run_regression_eval(
    run_name_prefix: str, session: Session, promotion_run_id: str
) -> float:
    prod_tables = session.exec(
        select(Table)
        .where(Table.status == TableStatus.production)
        .where(Table.owner_id != "spider2")
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
        total_questions=len(all_production_questions),
        status=EvalStatus.running,
        triggered_by="promotion-regression",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Send schema.table format so the agent can validate tables without ambiguity
    table_names = [
        f"{t.schema_name}.{t.name}" if t.schema_name else t.name for t in prod_tables
    ]
    try:
        req = {
            "dataset_name": PRODUCTION_DATASET_NAME,
            "additional_tables": table_names,
        }
        resp = requests.post(
            f"{settings.EVALUATION_SERVICE_URL}/text-to-sql/evaluation/run-single-dataset",
            json=req,
            timeout=600,
        )
        resp.raise_for_status()
        eval_resp = RunDatasetResponse(**resp.json())
    except Exception as e:
        logger.error(f"[Promotion/Phase-B] Regression eval failed: {e}")
        run.status = EvalStatus.failed
        run.score = 0.0
        run.total_questions = len(all_production_questions)
        session.add(run)
        session.commit()
        return 0.0

    _map_and_save_run_metrics(run, eval_resp, session, run.id)
    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Regression contains_score (with candidate) = {eval_resp.accuracy.contains_accuracy:.3f} "
        f"exact_score = {eval_resp.accuracy.execution_accuracy:.3f} exact_match = {eval_resp.accuracy.sql_exact_match:.3f}"
    )
    return eval_resp.accuracy.contains_accuracy


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


REGRESSION_BLOCK_DELTA = 0.10
REGRESSION_WARNING_DELTA = 0.05
LOW_SCORE_THRESHOLD = 0.70


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


@observe(name="eval-run")
def _run_evaluation_pipeline(table_id: str, run_id: str):
    with Session(engine) as session:
        run = session.get(EvalRun, run_id)
        if not run:
            return

        try:
            score = execute_single_table_eval(table_id, run_id, session)

            # Fetch updated run state
            session.refresh(run)

            # Detect regression vs previous run
            prev_runs = session.exec(
                select(EvalRun)
                .where(
                    EvalRun.table_id == table_id,
                    EvalRun.status == EvalStatus.completed,
                    EvalRun.id != run_id,
                )
                .order_by(EvalRun.created_at.desc())
                .limit(1)
            ).first()

            regression_detected = False
            regression_delta = None

            if prev_runs and prev_runs.score > 0:
                delta = prev_runs.score - score
                if delta > REGRESSION_BLOCK_DELTA:
                    regression_detected = True
                    regression_delta = round(delta, 4)
                    _create_alert(
                        session,
                        run_id,
                        table_id,
                        "regression",
                        AlertSeverity.critical,
                        f"Score dropped {delta:.1%} (from {prev_runs.score:.2f} → {score:.2f})",
                        {
                            "previous_score": prev_runs.score,
                            "current_score": score,
                            "delta": delta,
                        },
                    )
                elif delta > REGRESSION_WARNING_DELTA:
                    regression_detected = True
                    regression_delta = round(delta, 4)
                    _create_alert(
                        session,
                        run_id,
                        table_id,
                        "regression",
                        AlertSeverity.warning,
                        f"Score warning: {delta:.1%} drop detected",
                        {
                            "previous_score": prev_runs.score,
                            "current_score": score,
                            "delta": delta,
                        },
                    )

            # Low score alert
            if score < LOW_SCORE_THRESHOLD:
                _create_alert(
                    session,
                    run_id,
                    table_id,
                    "low_score",
                    AlertSeverity.warning,
                    f"Table performance is low ({score:.1%})",
                )

            # Finalize run orchestration fields
            run.regression_detected = regression_detected
            run.regression_delta = regression_delta
            session.add(run)

            # Persist metrics for analytics
            metrics_to_save = [
                ("accuracy", score if score is not None else 0.0),
            ]
            if run.dimension_averages:
                for dim_name, dim_val in run.dimension_averages.items():
                    metrics_to_save.append(
                        (dim_name, dim_val if dim_val is not None else 0.0)
                    )
            if run.failure_breakdown:
                for fail_type, count in run.failure_breakdown.items():
                    metrics_to_save.append(
                        (fail_type, float(count) if count is not None else 0.0)
                    )

            for metric_name, m_val in metrics_to_save:
                metric = EvaluationHistoryMetric(
                    run_id=run_id, metric_name=metric_name, metric_value=m_val
                )
                session.add(metric)

            session.commit()
        except Exception as e:
            logger.error(
                f"[Eval] Error in evaluation pipeline run {run_id}: {e}", exc_info=True
            )
            try:
                session.refresh(run)
                run.status = EvalStatus.failed
                run.score = 0.0
                session.add(run)
                session.commit()
            except Exception as db_err:
                logger.error(f"[Eval] Failed to mark run {run_id} as failed: {db_err}")


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

    run = EvalRun(
        table_id=table_id,
        total_questions=len(questions),
        status=EvalStatus.running,
    )
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
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where(EvalRun.id == run_id)
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Eval run not found")
    run, table_name = result
    if not table_name:
        if run.triggered_by == "promotion-baseline":
            table_name = "Production Baseline"
        elif run.triggered_by == "promotion-regression":
            table_name = "Production Regression"
        elif run.triggered_by:
            table_name = f"Dataset: {run.triggered_by}"
        else:
            table_name = "Unknown"
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
