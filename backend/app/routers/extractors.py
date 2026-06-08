import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from core.db.engine import get_session
from core.models.models import HttpExtractor, HttpExtractorCreate, HttpExtractorRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extractors", tags=["extractors"])

# TODO: make the edit/create/delete routes admin only

@router.get("", response_model=list[HttpExtractorRead])
def list_extractors(session: Session = Depends(get_session)):
    """List all registered HTTP extractors."""
    return session.exec(select(HttpExtractor)).all()

@router.post("", response_model=HttpExtractorRead, status_code=201)
def create_extractor(payload: HttpExtractorCreate, session: Session = Depends(get_session)):
    """Register a new HTTP extractor."""
    # Check if name exists
    existing = session.exec(select(HttpExtractor).where(HttpExtractor.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Extractor with this name already exists")
        
    extractor = HttpExtractor(**payload.model_dump())
    session.add(extractor)
    session.commit()
    session.refresh(extractor)
    return extractor

@router.delete("/{extractor_id}", status_code=204)
def delete_extractor(extractor_id: str, session: Session = Depends(get_session)):
    """Delete an HTTP extractor by ID."""
    extractor = session.get(HttpExtractor, extractor_id)
    if not extractor:
        raise HTTPException(status_code=404, detail="Extractor not found")
    session.delete(extractor)
    session.commit()
