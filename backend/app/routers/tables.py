import logging
from datetime import datetime

from core.db.engine import get_session
from core.models.models import (
    ForeignKeyMapping,
    ForeignKeyMappingCreate,
    ForeignKeyMappingRead,
    GoldenQuestion,
    Table,
    TableRead,
    TableStatus,
    UserScope,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel import Session, select

from app.routers.evaluation import PRODUCTION_DATASET_NAME, _build_questions_payload
from app.services.langfuse_client import langfuse_client

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/tables", tags=["tables"])


@router.get("", response_model=list[TableRead])
def list_tables(
    status: TableStatus | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    x_scope_id: str | None = Header(default=None),
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


@router.patch("/{table_id}/status", response_model=TableRead)
def update_table_status(
    table_id: str, status: TableStatus, session: Session = Depends(get_session)
):
    """
    Update a table's status.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    previous_status = table.status
    table.status = status
    table.updated_at = datetime.now()
    session.add(table)
    session.commit()
    session.refresh(table)

    # Handle transitions
    if status == TableStatus.production and previous_status != TableStatus.production:
        logger.info(
            f"[Tables] Table '{table.name}' approved for production. Adding to dataset."
        )

        # Upsert golden questions to production dataset
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        if qs:
            payload = _build_questions_payload(qs, table)
            langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, payload)

    elif (
        status in [TableStatus.sandbox, TableStatus.degraded]
        and previous_status == TableStatus.production
    ):
        logger.info(
            f"[Tables] Table '{table.name}' demoted from production -> {status.value}. Removing from dataset."
        )
        # Remove questions from production dataset
        langfuse_client.remove_table_questions_from_dataset(
            PRODUCTION_DATASET_NAME, table.id
        )

    return table


# ─────────────────────────────────────────────────────────────────────────────
# FOREIGN KEY MAPPINGS
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{table_id}/foreign-keys", response_model=list[ForeignKeyMappingRead])
def get_foreign_keys(table_id: str, session: Session = Depends(get_session)):
    """
    Get all custom foreign key mappings for a table.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    return session.exec(
        select(ForeignKeyMapping).where(ForeignKeyMapping.table_id == table_id)
    ).all()


@router.post("/{table_id}/foreign-keys", response_model=ForeignKeyMappingRead)
def create_foreign_key(
    table_id: str,
    payload: ForeignKeyMappingCreate,
    session: Session = Depends(get_session),
):
    """
    Create or update a foreign key mapping for a specific column in the table.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Source table not found")

    target_table = session.get(Table, payload.target_table_id)
    if not target_table:
        raise HTTPException(status_code=404, detail="Target table not found")

    # Default to 1-to-1 mapping for a given source column (Upsert behavior)
    existing = session.exec(
        select(ForeignKeyMapping)
        .where(ForeignKeyMapping.table_id == table_id)
        .where(ForeignKeyMapping.source_column == payload.source_column)
    ).first()

    if existing:
        existing.target_table_id = payload.target_table_id
        existing.target_column = payload.target_column
        existing.updated_at = datetime.now()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    mapping = ForeignKeyMapping(
        table_id=table_id,
        source_column=payload.source_column,
        target_table_id=payload.target_table_id,
        target_column=payload.target_column,
    )
    session.add(mapping)
    session.commit()
    session.refresh(mapping)
    return mapping


@router.delete("/{table_id}/foreign-keys/{fk_id}", status_code=204)
def delete_foreign_key(
    table_id: str, fk_id: str, session: Session = Depends(get_session)
):
    """
    Delete a specific foreign key mapping.
    """
    mapping = session.get(ForeignKeyMapping, fk_id)
    if not mapping or mapping.table_id != table_id:
        raise HTTPException(status_code=404, detail="Foreign key mapping not found")

    session.delete(mapping)
    session.commit()
