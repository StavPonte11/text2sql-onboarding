from fastapi import APIRouter, Depends, HTTPException, Query, Header
from typing import Optional, List
from sqlmodel import Session, select
from datetime import datetime
from app.db.engine import get_session
from app.models.models import (
    Table, TableCreate, TableRead,
    TableStatus, UserScope
)
from app.services.warehouse import remove_table_from_warehouse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["tables"])


@router.get("", response_model=List[TableRead])
def list_tables(
    status: Optional[TableStatus] = Query(default=None),
    owner_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    x_scope_id: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
):
    q = select(Table)
    if status:
        q = q.where(Table.status == status)
    if owner_id:
        q = q.where(Table.owner_id == owner_id)
    if search:
        q = q.where(Table.name.ilike(f"%{search}%"))

    if x_scope_id:
        scope = session.get(UserScope, x_scope_id)
        if scope:
            if "marketing" in scope.name.lower():
                q = q.where(Table.name.ilike("%campaign%"))
            elif "finance" in scope.name.lower():
                q = q.where(Table.name.ilike("%order%"))

    return session.exec(q).all()


@router.get("/{table_id}", response_model=TableRead)
def get_table(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return table


@router.post("", response_model=TableRead, status_code=201)
def create_table(payload: TableCreate, session: Session = Depends(get_session)):
    table = Table(**payload.model_dump())
    session.add(table)
    session.commit()
    session.refresh(table)
    return table


@router.patch("/{table_id}/status", response_model=TableRead)
def update_table_status(
    table_id: str,
    status: TableStatus,
    session: Session = Depends(get_session)
):
    """
    Update a table's status.

    If transitioning FROM production → sandbox:
      The table is removed from the data warehouse so the Text2SQL agent
      can no longer query it.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    previous_status = table.status
    table.status = status
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()
    session.refresh(table)

    from app.services.warehouse import add_table_to_warehouse, remove_table_from_warehouse
    from app.services.langfuse_client import langfuse_client
    from app.routers.evaluation import _build_questions_payload, PRODUCTION_DATASET_NAME
    from app.models.models import GoldenQuestion

    # Handle transitions
    if status == TableStatus.production and previous_status != TableStatus.production:
        logger.info(f"[Tables] Table '{table.name}' approved for production. Adding to warehouse and dataset.")
        add_table_to_warehouse(table)
        
        # Upsert golden questions to production dataset
        qs = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)).all()
        if qs:
            payload = _build_questions_payload(qs, table)
            langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, payload)
            
    elif status in [TableStatus.sandbox, TableStatus.degraded] and previous_status == TableStatus.production:
        logger.info(f"[Tables] Table '{table.name}' demoted from production -> {status.value}. Removing from warehouse and dataset.")
        remove_table_from_warehouse(table)
        
        # Remove questions from production dataset
        langfuse_client.remove_table_questions_from_dataset(PRODUCTION_DATASET_NAME, table.id)

    return table
