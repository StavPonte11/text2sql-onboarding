from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    GoldenQuestion, GoldenQuestionCreate, GoldenQuestionRead, Table
)

router = APIRouter(prefix="/tables", tags=["golden-questions"])


@router.get("/{table_id}/questions", response_model=list[GoldenQuestionRead])
def list_questions(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()


@router.post("/{table_id}/questions", response_model=GoldenQuestionRead, status_code=201)
def create_question(
    table_id: str,
    payload: GoldenQuestionCreate,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    q = GoldenQuestion(table_id=table_id, **payload.model_dump())
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


@router.delete("/{table_id}/questions/{question_id}", status_code=204)
def delete_question(
    table_id: str,
    question_id: str,
    session: Session = Depends(get_session),
):
    q = session.get(GoldenQuestion, question_id)
    if not q or q.table_id != table_id:
        raise HTTPException(status_code=404, detail="Question not found")
    session.delete(q)
    session.commit()
