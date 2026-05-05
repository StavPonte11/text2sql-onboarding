from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    GoldenQuestion, GoldenQuestionCreate, GoldenQuestionRead, Table
)
from langfuse import Langfuse

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
    
    # Sync to Langfuse Dataset
    try:
        langfuse = Langfuse()
        dataset_name = f"text2sql_{table_id[:8]}"
        
        # Ensure dataset exists (create_dataset is usually safe if it exists, or we handle gracefully)
        try:
            langfuse.create_dataset(name=dataset_name)
        except Exception:
            pass  # Dataset might already exist
            
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={"question": q.question},
            expected_output={"expected_sql": q.expected_sql},
            metadata={
                "question_id": q.id,
                "question_type": q.question_type.value if hasattr(q.question_type, 'value') else q.question_type,
                "difficulty": q.difficulty.value if hasattr(q.difficulty, 'value') else q.difficulty
            }
        )
    except Exception as e:
        print(f"Warning: Failed to sync question to Langfuse: {e}")

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
