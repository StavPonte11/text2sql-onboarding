"""
Feedback router — stores and retrieves user feedback (👍/👎) on queries.
Feedback signals are consumed by the Table Health scoring engine.
"""

from core.db.engine import get_session
from core.models.models import (
    AuditQuery,
    QueryFeedback,
    QueryFeedbackCreate,
    QueryFeedbackRead,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=QueryFeedbackRead, status_code=201)
def submit_feedback(
    payload: QueryFeedbackCreate, session: Session = Depends(get_session)
):
    # Validate query exists
    query = session.get(AuditQuery, payload.query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found in audit log")

    feedback = QueryFeedback(**payload.model_dump())
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


@router.get("/feedback/table/{table_id}", response_model=list[QueryFeedbackRead])
def get_table_feedback(
    table_id: str,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    return session.exec(
        select(QueryFeedback)
        .where(QueryFeedback.table_id == table_id)
        .order_by(QueryFeedback.created_at.desc())
        .limit(limit)
    ).all()


@router.get("/feedback/query/{query_id}", response_model=list[QueryFeedbackRead])
def get_query_feedback(query_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(QueryFeedback).where(QueryFeedback.query_id == query_id)
    ).all()
