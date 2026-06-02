"""
publish.py — Table publish workflow with regression gate.

Publish triggers an asynchronous promotion workflow that:
1. Re-evaluates the target table.
2. Runs regression tests on all production tables.
3. Only promotes if all pass.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from core.db.engine import get_session
from core.models.models import (
    EnrichmentVersion,
    EvalRun,
    EvalStatus,
    GoldenQuestion,
    Table,
)
from app.routers.evaluation import promote_table_to_production_workflow
from app.services.scoring import REGRESSION_BLOCK

router = APIRouter(prefix="/tables", tags=["publish"])


def _check_regression(table_id: str, new_score: float, session: Session) -> list[dict]:
    """
    Check if publishing this table causes a regression in any production table.
    """
    warnings = []
    all_runs = session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id, EvalRun.status == EvalStatus.completed)
        .order_by(EvalRun.created_at.desc())
        .limit(2)
    ).all()

    if len(all_runs) >= 2:
        prev_score = all_runs[1].score
        delta = prev_score - new_score
        if delta > REGRESSION_BLOCK:
            warnings.append(
                {
                    "code": "REGRESSION_BLOCK",
                    "message": f"Score dropped {delta:.0%} from previous run. Threshold: {REGRESSION_BLOCK:.0%}",
                    "severity": "blocking",
                }
            )
    return warnings


@router.post("/{table_id}/publish")
def publish_table(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    blocking_errors = []
    warnings = []

    # ── Gate 1: Enrichment ────────────────────────────────────────────────────
    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    if not enrichment:
        blocking_errors.append(
            {"code": "MISSING_ENRICHMENT", "message": "Enrichment required."}
        )

    # ── Gate 2: Golden Questions ───────────────────────────────────────────────
    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if len(questions) < 3:
        blocking_errors.append(
            {
                "code": "INSUFFICIENT_QUESTIONS",
                "message": "At least 3 golden questions required.",
            }
        )

    if blocking_errors:
        raise HTTPException(
            status_code=422,
            detail={"blocking_errors": blocking_errors, "warnings": warnings},
        )

    # ── Trigger Promotion Workflow (Async) ────────────────────────────────────
    run_id = str(uuid.uuid4())

    background_tasks.add_task(promote_table_to_production_workflow, table_id, run_id)

    return {
        "status": "publishing",
        "message": "Production promotion workflow started with regression tests.",
        "run_id": run_id,
    }
