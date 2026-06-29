"""
publish.py — Table publish workflow with regression gate.

Publish triggers an asynchronous promotion workflow that:
1. Re-evaluates the target table.
2. Runs regression tests on all production tables.
3. Only promotes if all pass.
"""

import uuid

from core.db.engine import get_session
from core.models.models import (
    EnrichmentVersion,
    GoldenQuestion,
    Table,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.routers.evaluation import promote_table_to_production_workflow

router = APIRouter(prefix="/tables", tags=["publish"])


@router.post("/{table_id}/publish")
def publish_table(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """
    Start the publishing workflow for a table after validating required prerequisites.
    
    Parameters:
    	table_id (str): The table to publish.
    
    Returns:
    	dict: A response containing the publishing status, message, and run ID.
    
    Raises:
    	HTTPException: Raised with status code 404 if the table is not found, or 422 if required enrichment or golden questions are missing.
    """
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
