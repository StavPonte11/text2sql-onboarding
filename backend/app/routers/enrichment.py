from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, col
from datetime import datetime
from app.db.engine import get_session
from app.models.models import (
    EnrichmentVersion, EnrichmentCreate, EnrichmentRead,
    Table, TableStatus
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["enrichment"])


@router.post("/{table_id}/enrichment", response_model=EnrichmentRead, status_code=201)
def create_enrichment(
    table_id: str,
    payload: EnrichmentCreate,
    session: Session = Depends(get_session),
):
    """
    Save a new enrichment version for a table.

    If the table is currently in 'production' and the new enrichment
    changes the table_description or schema fields, the table is
    automatically moved to 'degraded'.

    This forces a full re-evaluation cycle before the table can be
    promoted to production again.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    # Fetch the latest existing enrichment for change detection
    existing = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(col(EnrichmentVersion.version).desc())
    ).first()

    next_version = (existing.version + 1) if existing else 1

    # Detect meaningful changes on production tables
    if table.status == TableStatus.production and existing and existing.data:
        old_data = existing.data or {}
        new_data = payload.data or {}

        SENSITIVE_KEYS = {"table_description", "schema", "columns", "schema_name"}
        changed_keys = {
            k for k in SENSITIVE_KEYS
            if old_data.get(k) != new_data.get(k)
        }

        if changed_keys:
            logger.warning(
                f"[Enrichment] Production table '{table.name}' enrichment changed "
                f"({', '.join(changed_keys)}) → degraded."
            )
            table.status = TableStatus.degraded
            table.updated_at = datetime.utcnow()
            session.add(table)



    ev = EnrichmentVersion(
        table_id=table_id,
        version=next_version,
        data=payload.data,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.get("/{table_id}/enrichment/latest", response_model=EnrichmentRead)
def get_latest_enrichment(table_id: str, session: Session = Depends(get_session)):
    ev = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(col(EnrichmentVersion.version).desc())
    ).first()
    if not ev:
        raise HTTPException(status_code=404, detail="No enrichment found")
    return ev
