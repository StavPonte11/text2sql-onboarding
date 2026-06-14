import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from core.db.engine import get_session
from core.models.models import (
    EvalRun,
    EvalStatus,
    GoldenQuestion,
    SecurityUser,
    Table,
    TableStatus,
)
from app.services.auth import require_admin
from app.services.langfuse_client import langfuse_client
from app.core.auth import check_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tables", tags=["admin_approval"])

PRODUCTION_DATASET_NAME = "text2sql_production"


class RejectionNote(BaseModel):
    note: str




def _sync_questions_to_production_dataset(session: Session):
    """
    Perform a full, true sync of ALL production tables' golden questions
    into the shared 'text2sql_production' Langfuse dataset.

    This means:
      - Questions from every table whose status == production are the
        DESIRED state.
      - Any dataset item not matching a current production question is
        removed (prevents stale accumulation from demoted/rejected tables).
      - New or changed questions are added / re-created automatically.

    Should be called after every admin approval or rejection so the
    dataset always reflects the live production question set.
    """
    if not langfuse_client.enabled:
        logger.info(
            "[AdminApproval] Langfuse disabled — skipping production dataset sync"
        )
        return

    # Gather ALL production tables and their questions
    prod_tables = session.exec(
        select(Table).where(Table.status == TableStatus.production)
    ).all()

    if not prod_tables:
        logger.info(
            "[AdminApproval] No production tables found — "
            "clearing the production dataset of any leftover items."
        )
        langfuse_client.sync_dataset(PRODUCTION_DATASET_NAME, [])
        return

    all_questions_payload: list[dict] = []
    for table in prod_tables:
        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        for q in questions:
            all_questions_payload.append(
                {
                    "question_id": q.id,
                    "question_text": q.question,
                    "expected_sql": q.expected_sql or "",
                    "table_id": q.table_id,
                    "schema_name": table.schema_name,
                    "question_type": (
                        q.question_type.value
                        if hasattr(q.question_type, "value")
                        else str(q.question_type)
                    ),
                    "difficulty": (
                        q.difficulty.value
                        if hasattr(q.difficulty, "value")
                        else str(q.difficulty)
                    ),
                }
            )

    logger.info(
        f"[AdminApproval] Syncing {len(all_questions_payload)} questions "
        f"from {len(prod_tables)} production table(s) to '{PRODUCTION_DATASET_NAME}'"
    )

    try:
        langfuse_client.sync_dataset(PRODUCTION_DATASET_NAME, all_questions_payload)
        logger.info(
            f"[AdminApproval] Production dataset sync complete: "
            f"{len(all_questions_payload)} items."
        )
    except Exception as e:
        logger.error(f"[AdminApproval] Failed to sync production dataset: {e}")


@router.get("/pending", response_model=list[dict])
def get_pending_tables(
    current_admin: SecurityUser = Depends(check_admin),
    session: Session = Depends(get_session),
):
    """Get all tables in 'verified' status (awaiting admin approval)."""
    tables = session.exec(
        select(Table).where(Table.status == TableStatus.verified)
    ).all()

    result = []
    for table in tables:
        latest_run = session.exec(
            select(EvalRun)
            .where(EvalRun.table_id == table.id, EvalRun.status == EvalStatus.completed)
            .order_by(desc(EvalRun.created_at))
        ).first()

        result.append(
            {
                "id": table.id,
                "name": table.name,
                "schema_name": table.schema_name,
                "status": table.status,
                "latest_run": {
                    "score": latest_run.score if latest_run else None,
                    "pass_rate": latest_run.pass_rate if latest_run else None,
                    "regression_detected": latest_run.regression_detected
                    if latest_run
                    else None,
                    "regression_delta": latest_run.regression_delta
                    if latest_run
                    else None,
                    "created_at": latest_run.created_at if latest_run else None,
                }
                if latest_run
                else None,
            }
        )
    return result


@router.post("/{table_id}/approve")
def approve_table(
    table_id: str,
    current_admin: SecurityUser = Depends(check_admin),
    session: Session = Depends(get_session),
):
    """
    Approve a verified table for production.

    Actions:
      1. Set table.status = production
      2. Sync its golden questions to the shared 'text2sql_production' Langfuse dataset
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400,
            detail=f"Table must be in 'verified' status to be approved. Current: {table.status}",
        )

    # 1. Promote status
    table.status = TableStatus.production
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()

    # 2. Full sync of ALL production questions to the shared Langfuse dataset
    _sync_questions_to_production_dataset(session)

    logger.info(
        f"[AdminApproval] Admin '{current_admin.email}' approved table '{table.name}' → production"
    )
    return {
        "message": "Table approved for production",
        "table_id": table_id,
        "status": table.status,
    }


@router.post("/{table_id}/reject")
def reject_table(
    table_id: str,
    rejection: RejectionNote,
    current_admin: SecurityUser = Depends(check_admin),
    session: Session = Depends(get_session),
):
    """
    Reject a verified table, returning it to sandbox.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400,
            detail=f"Table must be in 'verified' status to be rejected. Current: {table.status}",
        )

    table.status = TableStatus.sandbox
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()

    # Sync the production dataset so rejected table's questions are removed
    _sync_questions_to_production_dataset(session)

    logger.info(
        f"[AdminApproval] Admin '{current_admin.email}' rejected table '{table.name}' "
        f"→ sandbox. Reason: {rejection.note}"
    )
    return {
        "message": "Table rejected and returned to sandbox",
        "table_id": table_id,
        "status": table.status,
        "note": rejection.note,
    }
