"""
orchestration.py — Evaluation Orchestration & Monitoring APIs.

New endpoints:
  Schedules:   GET/POST/PUT/DELETE /evaluations/schedules
  Runs:        POST /evaluations/run, GET /evaluations/runs, GET /evaluations/runs/{id}
  Analytics:   GET /evaluations/analytics/trends, /analytics/tables
  Comparison:  GET /evaluations/compare?run1=&run2=
  Alerts:      GET /evaluations/alerts, POST /evaluations/alerts/{id}/ack
  System:      GET /evaluations/system-health
"""

import random
from datetime import UTC, datetime, timedelta

from core.db.engine import engine, get_session
from core.models.models import (
    AlertSeverity,
    EnrichmentVersion,
    EvalResult,
    EvalRun,
    EvalRunRead,
    EvalStatus,
    EvaluationAlert,
    EvaluationAlertRead,
    EvaluationHistoryMetric,
    EvaluationSchedule,
    EvaluationScheduleCreate,
    EvaluationScheduleRead,
    EvaluationScheduleUpdate,
    GoldenQuestion,
    Table,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from langfuse.decorators import langfuse_context, observe
from sqlmodel import Session, select

from app.routers.evaluation import execute_single_table_eval
from app.services.trino_client import execute_query_sync

router = APIRouter(prefix="/evaluations", tags=["evaluation-orchestration"])

# ── Constants ──────────────────────────────────────────────────────────────────
REGRESSION_BLOCK_DELTA = 0.10
REGRESSION_WARNING_DELTA = 0.05
LOW_SCORE_THRESHOLD = 0.70


# ── Stubbed external calls (same as in evaluation.py) ─────────────────────────


@observe(as_type="generation", name="text2sql_agent")
def run_text2sql_agent(question: str, table_id: str) -> dict:
    """Simulates the LangGraph Text2SQL agent flow with up to 4 refinement iterations."""
    # In reality, this would invoke the LangGraph text2sql workflow
    # For evaluation, we execute the generated SQL against real Trino

    generated_sql = f'SELECT id, name, value FROM "{table_id[:8]}" LIMIT 100'

    # Execute against Trino
    trino_result = execute_query_sync(generated_sql, table_id)

    iterations = random.choices([1, 2, 3, 4], weights=[60, 25, 10, 5])[0]

    langfuse_context.update_current_trace(
        tags=["evaluation", f"table_{table_id[:8]}"],
        metadata={"iterations": iterations, "success": trino_result.success},
    )

    return {
        "generated_sql": generated_sql,
        "tables_used": [f"{table_id[:8]}"],
        "generated_columns": trino_result.columns,
        "refiner_iterations": iterations,
        "execution": {
            "success": trino_result.success,
            "rows": trino_result.rows,
            "columns": trino_result.columns,
            "row_count": trino_result.row_count,
            "execution_time_ms": trino_result.execution_time_ms,
            "error_message": trino_result.error_message,
        },
    }


def _stub_judge(exec_success: bool) -> dict:
    # INCREASED BASE SCORE: 0.70 to 0.95 for success, 0.2 to 0.45 for fail
    base = random.uniform(0.70, 0.95) if exec_success else random.uniform(0.20, 0.45)
    failure_types = [None, None, None, "wrong_table", "wrong_join", "wrong_filter"]
    return {
        "table_selection_correctness": round(
            min(1.0, base + random.uniform(-0.1, 0.1)), 3
        ),
        "sql_semantic_equivalence": round(
            min(1.0, base + random.uniform(-0.15, 0.1)), 3
        ),
        "result_correctness": round(min(1.0, base + random.uniform(-0.05, 0.1)), 3),
        "hallucination_detected": random.random() < 0.05,
        "failure_type": random.choice(failure_types) if not exec_success else None,
        "reasoning": {},
        "confidence_in_judgment": round(random.uniform(0.7, 0.95), 3),
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────


@observe(name="evaluation_pipeline")
def _run_full_pipeline(
    table_ids: list[str], run_ids: list[str], triggered_by: str = "user"
):
    """Run evaluation for multiple tables (one run per table)."""
    langfuse_context.update_current_trace(
        tags=["evaluation_run"],
        metadata={
            "table_ids": table_ids,
            "run_ids": run_ids,
            "triggered_by": triggered_by,
        },
    )

    for table_id, run_id in zip(table_ids, run_ids, strict=False):
        with Session(engine) as session:
            run = session.get(EvalRun, run_id)
            if not run:
                continue

            # ── Delegate to shared core logic ───────────────────────────
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


# ── Runs ──────────────────────────────────────────────────────────────────────


@router.post("/run", response_model=list[EvalRunRead], status_code=202)
def trigger_evaluation_run(
    table_ids: list[str],
    background_tasks: BackgroundTasks,
    triggered_by: str = Query(default="user"),
    session: Session = Depends(get_session),
):
    """Trigger evaluation for one or more tables."""

    # ── Validate all tables before creating any run records ─────────────────
    validation_errors: list[str] = []
    for table_id in table_ids:
        table = session.get(Table, table_id)
        if not table:
            raise HTTPException(status_code=404, detail=f"Table {table_id} not found")

        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table_id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()

        missing: list[str] = []
        if not enrichment or not enrichment.data:
            missing.append("table enrichment / schema description")
        elif not enrichment.data.get("table_description"):
            missing.append("table description (enrichment has no description)")

        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
        ).all()
        if not questions:
            missing.append("golden questions (at least 1 required)")

        if missing:
            validation_errors.append(f"'{table.name}': {', '.join(missing)}")

    if validation_errors:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot run evaluation. The following tables are missing required data — {'; '.join(validation_errors)}",
        )

    # ── All tables passed — create run records ───────────────────────────────
    runs = []
    for table_id in table_ids:
        table = session.get(Table, table_id)
        run = EvalRun(table_id=table_id, triggered_by=triggered_by)
        session.add(run)
        session.commit()
        session.refresh(run)
        runs.append(run)

    background_tasks.add_task(
        _run_full_pipeline,
        [r.table_id for r in runs],
        [r.id for r in runs],
        triggered_by,
    )
    read_runs = []
    for r in runs:
        t = session.get(Table, r.table_id)
        read_runs.append(
            EvalRunRead.model_validate(
                r, update={"table_name": t.name if t else r.table_id}
            )
        )
    return read_runs


@router.get("/runs", response_model=list[EvalRunRead])
def list_runs(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    status: str | None = Query(default=None),
    table_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    query = (
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .order_by(EvalRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(EvalRun.status == status)
    if table_id:
        query = query.where(EvalRun.table_id == table_id)

    results = session.exec(query).all()
    runs = []
    for run, table_name in results:
        t_name = table_name if table_name else "All prod tables"
        read = EvalRunRead.model_validate(run, update={"table_name": t_name})
        runs.append(read)
    return runs


@router.get("/runs/{run_id}", response_model=EvalRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    result = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.id == run_id)
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Eval run not found")

    run, table_name = result
    t_name = table_name if table_name else "All prod tables"
    return EvalRunRead.model_validate(run, update={"table_name": t_name})


@router.get("/runs/{run_id}/report")
def get_run_report(run_id: str, session: Session = Depends(get_session)):
    """Full structured report — matches the evaluation_pipeline.md format."""
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    results = session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()

    # fetch questions text for each question
    q_ids = [r.question_id for r in results]
    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.id.in_(q_ids))
    ).all()
    question_map = {q.id: q.question for q in questions}

    return {
        "run_id": run_id,
        "table_id": run.table_id,
        "overall_score": run.score,
        "pass_rate": run.pass_rate,
        "fail_rate": run.fail_rate,
        "total_questions": run.total_questions,
        "duration_seconds": run.duration_seconds,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "promotion_run_id": run.promotion_run_id,
        "is_publishable": run.score > 0.00,
        "regression_detected": run.regression_detected,
        "regression_delta": run.regression_delta,
        "failure_breakdown": run.failure_breakdown or {},
        "dimension_averages": run.dimension_averages or {},
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "per_question": [
            {
                "question_id": r.question_id,
                "question": question_map.get(r.question_id),
                "score": r.score,
                "status": r.status,
                "failure_type": r.error_type,
            }
            for r in results
        ],
    }


# ── Schedules ─────────────────────────────────────────────────────────────────


@router.get("/schedules", response_model=list[EvaluationScheduleRead])
def list_schedules(session: Session = Depends(get_session)):
    return session.exec(
        select(EvaluationSchedule).order_by(EvaluationSchedule.created_at.desc())
    ).all()


@router.post("/schedules", response_model=EvaluationScheduleRead, status_code=201)
def create_schedule(
    payload: EvaluationScheduleCreate, session: Session = Depends(get_session)
):
    schedule = EvaluationSchedule(**payload.model_dump())
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


@router.put("/schedules/{schedule_id}", response_model=EvaluationScheduleRead)
def update_schedule(
    schedule_id: str,
    payload: EvaluationScheduleUpdate,
    session: Session = Depends(get_session),
):
    schedule = session.get(EvaluationSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(schedule, k, v)
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, session: Session = Depends(get_session)):
    schedule = session.get(EvaluationSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    session.delete(schedule)
    session.commit()


# ── Analytics ─────────────────────────────────────────────────────────────────


@router.get("/analytics/trends")
def get_trends(
    days: int = Query(default=30),
    table_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Score and pass_rate over time. Returns one data point per completed run."""
    since = datetime.now(UTC) - timedelta(days=days)
    query = (
        select(EvalRun)
        .where(EvalRun.status == EvalStatus.completed, EvalRun.created_at >= since)
        .order_by(EvalRun.created_at.asc())
    )
    if table_id:
        query = query.where(EvalRun.table_id == table_id)

    runs = session.exec(query).all()

    # Group by day for sparkline data
    points = []
    for r in runs:
        # Get table name for each run (could be optimized with a join above)
        table = session.get(Table, r.table_id)
        points.append(
            {
                "run_id": r.id,
                "table_id": r.table_id,
                "table_name": table.name if table else r.table_id,
                "date": r.created_at.strftime("%Y-%m-%d"),
                "timestamp": r.created_at.isoformat(),
                "score": round(r.score, 3),
                "pass_rate": round(r.pass_rate, 3),
                "fail_rate": round(r.fail_rate, 3),
                "regression_detected": r.regression_detected,
            }
        )

    # Aggregate by date
    by_date: dict = {}
    for p in points:
        d = p["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "scores": [], "pass_rates": [], "run_count": 0}
        by_date[d]["scores"].append(p["score"])
        by_date[d]["pass_rates"].append(p["pass_rate"])
        by_date[d]["run_count"] += 1

    daily = []
    for d, v in sorted(by_date.items()):
        daily.append(
            {
                "date": d,
                "avg_score": round(sum(v["scores"]) / len(v["scores"]), 3),
                "avg_pass_rate": round(sum(v["pass_rates"]) / len(v["pass_rates"]), 3),
                "run_count": v["run_count"],
            }
        )

    return {"runs": points, "daily": daily, "total_runs": len(runs)}


@router.get("/analytics/tables")
def get_table_analytics(session: Session = Depends(get_session)):
    """Per-table performance ranking."""
    tables = session.exec(select(Table)).all()
    result = []
    for t in tables:
        runs = session.exec(
            select(EvalRun)
            .where(EvalRun.table_id == t.id, EvalRun.status == EvalStatus.completed)
            .order_by(EvalRun.created_at.desc())
            .limit(10)
        ).all()

        if not runs:
            result.append(
                {
                    "table_id": t.id,
                    "table_name": t.name,
                    "status": t.status,
                    "latest_score": None,
                    "avg_score": None,
                    "pass_rate": None,
                    "run_count": 0,
                    "trend": "stable",
                    "failure_breakdown": {},
                }
            )
            continue

        latest = runs[0]
        scores = [r.score for r in runs]
        avg = round(sum(scores) / len(scores), 3)

        # Trend: compare latest 3 vs previous 3
        trend = "stable"
        if len(scores) >= 4:
            recent_avg = sum(scores[:2]) / 2
            older_avg = sum(scores[2:4]) / 2
            if recent_avg < older_avg - 0.05:
                trend = "declining"
            elif recent_avg > older_avg + 0.05:
                trend = "improving"

        result.append(
            {
                "table_id": t.id,
                "table_name": t.name,
                "status": t.status,
                "latest_score": round(latest.score, 3),
                "avg_score": avg,
                "pass_rate": round(latest.pass_rate, 3),
                "run_count": len(runs),
                "trend": trend,
                "last_run_at": latest.created_at.isoformat(),
                "failure_breakdown": latest.failure_breakdown or {},
            }
        )

    result.sort(key=lambda x: (x["latest_score"] is None, x["latest_score"] or 0))
    return result


@router.get("/compare")
def compare_runs(
    run1: str = Query(...),
    run2: str = Query(...),
    session: Session = Depends(get_session),
):
    """Side-by-side comparison of two eval runs."""
    r1 = session.get(EvalRun, run1)
    r2 = session.get(EvalRun, run2)
    if not r1 or not r2:
        raise HTTPException(status_code=404, detail="One or both runs not found")

    res1 = {
        r.question_id: r
        for r in session.exec(select(EvalResult).where(EvalResult.run_id == run1)).all()
    }
    res2 = {
        r.question_id: r
        for r in session.exec(select(EvalResult).where(EvalResult.run_id == run2)).all()
    }

    all_qids = set(res1) | set(res2)
    regressions = []
    improvements = []

    for qid in all_qids:
        s1 = res1[qid].score if qid in res1 else None
        s2 = res2[qid].score if qid in res2 else None
        if s1 is not None and s2 is not None:
            delta = s2 - s1
            if delta < -0.1:
                regressions.append(
                    {
                        "question_id": qid,
                        "run1_score": s1,
                        "run2_score": s2,
                        "delta": round(delta, 3),
                    }
                )
            elif delta > 0.1:
                improvements.append(
                    {
                        "question_id": qid,
                        "run1_score": s1,
                        "run2_score": s2,
                        "delta": round(delta, 3),
                    }
                )

    score_delta = round(r2.score - r1.score, 4)

    return {
        "run1": {
            "id": r1.id,
            "score": r1.score,
            "pass_rate": r1.pass_rate,
            "created_at": r1.created_at.isoformat(),
            "table_id": r1.table_id,
        },
        "run2": {
            "id": r2.id,
            "score": r2.score,
            "pass_rate": r2.pass_rate,
            "created_at": r2.created_at.isoformat(),
            "table_id": r2.table_id,
        },
        "score_delta": score_delta,
        "pass_rate_delta": round(r2.pass_rate - r1.pass_rate, 4),
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "regressions": sorted(regressions, key=lambda x: x["delta"]),
        "improvements": sorted(improvements, key=lambda x: x["delta"], reverse=True),
        "verdict": "regression"
        if score_delta < -0.05
        else "improvement"
        if score_delta > 0.05
        else "stable",
    }


# ── Alerts ────────────────────────────────────────────────────────────────────


@router.get("/alerts", response_model=list[EvaluationAlertRead])
def list_alerts(
    acknowledged: bool | None = Query(default=None),
    limit: int = Query(default=50),
    session: Session = Depends(get_session),
):
    query = (
        select(EvaluationAlert).order_by(EvaluationAlert.created_at.desc()).limit(limit)
    )
    if acknowledged is not None:
        query = query.where(EvaluationAlert.acknowledged == acknowledged)
    return session.exec(query).all()


@router.post("/alerts/{alert_id}/acknowledge", response_model=EvaluationAlertRead)
def acknowledge_alert(alert_id: str, session: Session = Depends(get_session)):
    alert = session.get(EvaluationAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


# ── System Health ──────────────────────────────────────────────────────────────


@router.get("/system-health")
def system_health(session: Session = Depends(get_session)):
    """Aggregate system health for the control center dashboard."""
    all_runs = session.exec(
        select(EvalRun)
        .where(EvalRun.status == EvalStatus.completed)
        .order_by(EvalRun.created_at.desc())
        .limit(100)
    ).all()

    unacked_alerts = session.exec(
        select(EvaluationAlert).where(not EvaluationAlert.acknowledged)
    ).all()

    total_tables = session.exec(select(Table)).all()
    production_tables = [t for t in total_tables if t.status == "production"]

    latest_run = all_runs[0] if all_runs else None

    if all_runs:
        valid_scores = [r.score for r in all_runs if r.score is not None]
        global_score = (
            round(sum(valid_scores) / len(valid_scores), 3) if valid_scores else None
        )

        valid_pass_rates = [r.pass_rate for r in all_runs if r.pass_rate is not None]
        global_pass_rate = (
            round(sum(valid_pass_rates) / len(valid_pass_rates), 3)
            if valid_pass_rates
            else None
        )
    else:
        global_score = None
        global_pass_rate = None

    # Top failing tables (lowest avg score)
    table_scores: dict[str, list[float]] = {}
    for r in all_runs:
        if r.score is not None:
            table_scores.setdefault(r.table_id, []).append(r.score)

    failing_tables = []
    for tid, scores in table_scores.items():
        avg = sum(scores) / len(scores)
        if avg < LOW_SCORE_THRESHOLD:
            table = session.get(Table, tid) if tid else None
            failing_tables.append(
                {
                    "table_id": tid,
                    "table_name": table.name
                    if table
                    else (tid[:8] if tid else "Unknown"),
                    "avg_score": round(avg, 3),
                    "failure_rate": round(1 - avg, 3),
                }
            )

    failing_tables.sort(key=lambda x: x["avg_score"])  # type: ignore[return-value]

    # Recent runs (last 10)
    recent_runs = [
        {
            "run_id": r.id,
            "table_id": r.table_id,
            "score": r.score,
            "pass_rate": r.pass_rate,
            "status": r.status,
            "triggered_by": r.triggered_by,
            "created_at": r.created_at.isoformat(),
            "regression_detected": r.regression_detected,
        }
        for r in all_runs[:10]
    ]

    return {
        "global_score": global_score,
        "global_pass_rate": global_pass_rate,
        "active_alerts": len(unacked_alerts),
        "critical_alerts": sum(
            1 for a in unacked_alerts if a.severity == AlertSeverity.critical
        ),
        "last_evaluation": latest_run.created_at.isoformat() if latest_run else None,
        "total_tables": len(total_tables),
        "production_tables": len(production_tables),
        "total_runs_today": sum(
            1 for r in all_runs if r.created_at.date() == datetime.now(UTC).date()
        ),
        "top_failing_tables": failing_tables[:5],
        "recent_runs": recent_runs,
        "system_status": "critical"
        if any(a.severity == AlertSeverity.critical for a in unacked_alerts)
        else "warning"
        if unacked_alerts
        else "healthy",
    }
