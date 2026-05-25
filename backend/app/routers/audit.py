from fastapi import APIRouter, Depends, Query
from typing import Optional, List

from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import AuditQuery, AuditQueryRead

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/queries", response_model=List[AuditQueryRead])
def list_audit_queries(
    table_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    session: Session = Depends(get_session),
):
    q = select(AuditQuery).order_by(AuditQuery.created_at.desc()).limit(limit)
    if table_id:
        q = q.where(AuditQuery.table_id == table_id)
    if user_id:
        q = q.where(AuditQuery.user_id == user_id)
    return session.exec(q).all()
