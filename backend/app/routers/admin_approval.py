from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, desc
from pydantic import BaseModel
from datetime import datetime
from app.db.engine import get_session
from app.models.models import Table, TableStatus, Admin, EvalRun, EvalStatus, GoldenQuestion
from app.services.auth import get_current_admin
from app.services.warehouse import add_table_to_warehouse, remove_table_from_warehouse
from app.services.langfuse_client import langfuse_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tables", tags=["admin_approval"])

PRODUCTION_DATASET_NAME = "text2sql_production"


class RejectionNote(BaseModel):
    note: str


def _sync_questions_to_production_dataset(table: Table, session: Session):
    """
    Appends the newly approved table's golden questions to the shared
    'text2sql_production' Langfuse dataset.

    Uses append_questions_to_dataset (keyed on question_id) so:
      - This is idempotent — calling it twice won't create duplicates.
      - Existing questions from other production tables are never touched.
      - If the dataset doesn't exist yet it will be created automatically.
    """
    if not langfuse_client.enabled:
        logger.info(f"[AdminApproval] Langfuse disabled — skipping dataset sync for '{table.name}'")
        return

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
    ).all()

    if not questions:
        logger.warning(f"[AdminApproval] No golden questions found for '{table.name}' — skipping sync")
        return

    payload = [
        {
            "question_id": q.id,
            "question_text": q.question,
            "expected_sql": q.expected_sql or "",
            "table_id": q.table_id,
            "schema_name": table.schema_name,
            "question_type": q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
            "difficulty": q.difficulty.value if hasattr(q.difficulty, "value") else str(q.difficulty),
        }
        for q in questions
    ]

    try:
        ok = langfuse_client.append_questions_to_dataset(PRODUCTION_DATASET_NAME, payload)
        if ok:
            logger.info(
                f"[AdminApproval] Appended {len(payload)} questions for '{table.name}' "
                f"to '{PRODUCTION_DATASET_NAME}'"
            )
        else:
            logger.warning(
                f"[AdminApproval] Some questions for '{table.name}' could not be appended "
                f"to '{PRODUCTION_DATASET_NAME}'"
            )
    except Exception as e:
        logger.error(f"[AdminApproval] Failed to append questions to production dataset: {e}")



@router.get("/pending", response_model=List[dict])
def get_pending_tables(
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Get all tables in 'verified' status (awaiting admin approval)."""
    tables = session.exec(select(Table).where(Table.status == TableStatus.verified)).all()

    result = []
    for table in tables:
        latest_run = session.exec(
            select(EvalRun)
            .where(EvalRun.table_id == table.id, EvalRun.status == EvalStatus.completed)
            .order_by(desc(EvalRun.created_at))
        ).first()

        result.append({
            "id": table.id,
            "name": table.name,
            "schema_name": table.schema_name,
            "status": table.status,
            "latest_run": {
                "score": latest_run.score if latest_run else None,
                "pass_rate": latest_run.pass_rate if latest_run else None,
                "regression_detected": latest_run.regression_detected if latest_run else None,
                "regression_delta": latest_run.regression_delta if latest_run else None,
                "created_at": latest_run.created_at if latest_run else None,
            } if latest_run else None,
        })
    return result


@router.post("/{table_id}/approve")
def approve_table(
    table_id: str,
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Approve a verified table for production.

    Actions:
      1. Set table.status = production
      2. Add the table to the data warehouse (physical PostgreSQL schema)
      3. Sync its golden questions to the shared 'text2sql_production' Langfuse dataset
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400,
            detail=f"Table must be in 'verified' status to be approved. Current: {table.status}"
        )

    # 1. Promote status
    table.status = TableStatus.production
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()

    # 2. Add to warehouse (real PostgreSQL DDL)
    added = add_table_to_warehouse(table)
    if not added:
        logger.warning(
            f"[AdminApproval] Could not add '{table.name}' to warehouse after approval — "
            f"status already set to production. Manual intervention may be needed."
        )

    # 3. Sync questions to shared production Langfuse dataset
    _sync_questions_to_production_dataset(table, session)

    # 4. (Removed) We no longer delete the candidate dataset so that historical traces remain accessible.

    logger.info(
        f"[AdminApproval] Admin '{current_admin.username}' approved table '{table.name}' → production"
    )
    return {
        "message": "Table approved for production",
        "table_id": table_id,
        "status": table.status,
        "warehouse_added": added,
    }


@router.post("/{table_id}/reject")
def reject_table(
    table_id: str,
    rejection: RejectionNote,
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Reject a verified table, returning it to sandbox.

    No warehouse action is taken — the table was never permanently added
    (it was only temporarily inserted during the evaluation and then removed).
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400,
            detail=f"Table must be in 'verified' status to be rejected. Current: {table.status}"
        )

    table.status = TableStatus.sandbox
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()

    logger.info(
        f"[AdminApproval] Admin '{current_admin.username}' rejected table '{table.name}' "
        f"→ sandbox. Reason: {rejection.note}"
    )
    return {
        "message": "Table rejected and returned to sandbox",
        "table_id": table_id,
        "status": table.status,
        "note": rejection.note,
    }
