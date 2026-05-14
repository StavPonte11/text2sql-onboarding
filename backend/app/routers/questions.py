import io
import json
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import List

from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    GoldenQuestion, GoldenQuestionCreate, GoldenQuestionRead, Table, QuestionType, DifficultyLevel
)
from app.services.langfuse_client import langfuse_client

router = APIRouter(prefix="/tables", tags=["golden-questions"])


@router.get("/{table_id}/questions", response_model=List[GoldenQuestionRead])
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
    
    # Sync to Langfuse Dataset (uses shared singleton client)
    langfuse_client.sync_question_to_dataset(
        table_id=table_id,
        schema_name=table.schema_name,
        question_id=q.id,
        question_text=q.question,
        expected_sql=q.expected_sql,
        question_type=q.question_type.value,
        difficulty=q.difficulty.value,
    )

    return q


@router.post("/{table_id}/questions/upload", status_code=201)
async def upload_questions(
    table_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    contents = await file.read()
    questions_data = []

    try:
        if file.filename.endswith(".json"):
            questions_data = json.loads(contents)
        elif file.filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents))
            # Clean up column names and handle NaN
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            questions_data = df.where(pd.notnull(df), None).to_dict(orient="records")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format. Use JSON or Excel.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error parsing file: {str(e)}")

    created_count = 0
    for q_item in questions_data:
        # Validate required fields
        question_text = q_item.get("question") or q_item.get("question_text")
        expected_sql = q_item.get("expected_sql") or q_item.get("sql")
        
        if not question_text or not expected_sql:
            continue
            
        # Parse enums with defaults
        difficulty = q_item.get("difficulty", "simple")
        if difficulty not in [d.value for d in DifficultyLevel]:
            difficulty = "simple"
            
        q_type = q_item.get("question_type", "simple")
        if q_type not in [t.value for t in QuestionType]:
            q_type = "simple"

        q = GoldenQuestion(
            table_id=table_id,
            question=question_text,
            expected_sql=expected_sql,
            difficulty=DifficultyLevel(difficulty),
            question_type=QuestionType(q_type)
        )
        session.add(q)
        session.flush() # get ID without commit
        
        # Sync to Langfuse
        langfuse_client.sync_question_to_dataset(
            table_id=table_id,
            schema_name=table.schema_name,
            question_id=q.id,
            question_text=q.question,
            expected_sql=q.expected_sql,
            question_type=q.question_type.value,
            difficulty=q.difficulty.value,
        )
        created_count += 1

    session.commit()
    return {"message": f"Successfully uploaded {created_count} questions"}


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
