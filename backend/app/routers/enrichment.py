from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    EnrichmentVersion, EnrichmentCreate, EnrichmentRead,
    Table
)

router = APIRouter(prefix="/tables", tags=["enrichment"])


@router.post("/{table_id}/enrichment", response_model=EnrichmentRead, status_code=201)
def create_enrichment(
    table_id: str,
    payload: EnrichmentCreate,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    # Bump version
    existing = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    next_version = (existing.version + 1) if existing else 1

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
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    if not ev:
        raise HTTPException(status_code=404, detail="No enrichment found")
    return ev
