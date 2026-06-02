"""
Table Health router — computes and stores a composite health score per table.

Score formula (all normalized 0-1):
  health_score = 0.4 * eval_success_rate
               + 0.3 * feedback_ratio
               + 0.2 * data_quality_score
               + 0.1 * (0 if schema_drift else 1)

health_status:
  >= 0.75 → good
  >= 0.45 → warning
  <  0.45 → critical
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from core.db.engine import get_session
from core.models.models import (
    EvalResult,
    EvalRun,
    EvalStatus,
    FeedbackRating,
    HealthStatus,
    ProfilingStatus,
    QueryFeedback,
    Table,
    TableHealth,
    TableHealthRead,
    TableProfile,
)

router = APIRouter(tags=["health"])


def _compute_health(table_id: str, session: Session) -> TableHealth:
    """Recompute health metrics from live data and upsert the TableHealth record."""

    # 1. Eval success rate — from latest completed run's score
    latest_run = session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id, EvalRun.status == EvalStatus.completed)
        .order_by(EvalRun.created_at.desc())
    ).first()
    eval_success_rate = latest_run.score if latest_run else None

    # 2. Failure breakdown — from EvalResults linked to this table's runs
    all_runs = session.exec(select(EvalRun).where(EvalRun.table_id == table_id)).all()
    run_ids = [r.id for r in all_runs]

    failure_wrong_table = failure_wrong_sql = failure_empty_result = (
        failure_exec_error
    ) = 0
    if run_ids:
        results = session.exec(
            select(EvalResult).where(
                EvalResult.run_id.in_(run_ids), EvalResult.status == "fail"
            )
        ).all()
        for r in results:
            et = r.error_type or ""
            if et == "wrong_table":
                failure_wrong_table += 1
            elif et == "syntax_error":
                failure_wrong_sql += 1
            elif et == "empty_result":
                failure_empty_result += 1
            elif et == "execution_error":
                failure_exec_error += 1
            else:
                failure_wrong_sql += 1  # default bucket

    # 3. Feedback ratio
    feedback_all = session.exec(
        select(QueryFeedback).where(QueryFeedback.table_id == table_id)
    ).all()
    feedback_ratio: float | None = None
    if feedback_all:
        pos = sum(1 for f in feedback_all if f.rating == FeedbackRating.positive)
        feedback_ratio = round(pos / len(feedback_all), 3)

    # 4. Data quality score — from latest completed profile null_rate_avg (inverted)
    latest_profile = session.exec(
        select(TableProfile)
        .where(
            TableProfile.table_id == table_id,
            TableProfile.status == ProfilingStatus.completed,
        )
        .order_by(TableProfile.created_at.desc())
    ).first()
    data_quality_score: float | None = None
    if latest_profile and latest_profile.null_rate_avg is not None:
        data_quality_score = round(1.0 - latest_profile.null_rate_avg, 3)

    # 5. Composite score
    components = [
        (0.4, eval_success_rate),
        (0.3, feedback_ratio),
        (0.2, data_quality_score),
        (0.1, 1.0),  # schema drift — assume clean for now
    ]
    available = [(w, v) for w, v in components if v is not None]
    if available:
        total_weight = sum(w for w, _ in available)
        health_score = round(sum(w * v for w, v in available) / total_weight, 3)
    else:
        health_score = 0.0

    health_status = (
        HealthStatus.good
        if health_score >= 0.75
        else HealthStatus.warning
        if health_score >= 0.45
        else HealthStatus.critical
    )

    # Upsert
    existing = session.exec(
        select(TableHealth).where(TableHealth.table_id == table_id)
    ).first()
    if existing:
        existing.health_score = health_score
        existing.health_status = health_status
        existing.eval_success_rate = eval_success_rate
        existing.feedback_ratio = feedback_ratio
        existing.data_quality_score = data_quality_score
        existing.failure_wrong_table = failure_wrong_table
        existing.failure_wrong_sql = failure_wrong_sql
        existing.failure_empty_result = failure_empty_result
        existing.failure_execution_error = failure_exec_error
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    else:
        health = TableHealth(
            table_id=table_id,
            health_score=health_score,
            health_status=health_status,
            eval_success_rate=eval_success_rate,
            feedback_ratio=feedback_ratio,
            data_quality_score=data_quality_score,
            failure_wrong_table=failure_wrong_table,
            failure_wrong_sql=failure_wrong_sql,
            failure_empty_result=failure_empty_result,
            failure_execution_error=failure_exec_error,
        )
        session.add(health)
        session.commit()
        session.refresh(health)
        return health


@router.get("/tables/{table_id}/health", response_model=TableHealthRead)
def get_table_health(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return _compute_health(table_id, session)


@router.post("/tables/{table_id}/health/recompute", response_model=TableHealthRead)
def recompute_health(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return _compute_health(table_id, session)


@router.get("/health/all", response_model=list[TableHealthRead])
def get_all_health(session: Session = Depends(get_session)):
    """Returns the latest health record for every table (used in the table list view)."""
    return session.exec(
        select(TableHealth).order_by(TableHealth.health_score.asc())
    ).all()
