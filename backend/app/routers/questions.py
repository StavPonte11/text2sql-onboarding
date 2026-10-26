import io
import json
import logging

import pandas as pd
from core.db.engine import get_session
from core.models.models import (
    DifficultyLevel,
    GoldenQuestion,
    GoldenQuestionCreate,
    GoldenQuestionRead,
    QuestionType,
    Table,
)
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.services.jeen_metadata import fetch_jeen_sql_pairs, save_jeen_sql_pair

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["golden-questions"])


@router.get("/{table_id}/questions", response_model=list[GoldenQuestionRead])
def list_questions(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()


@router.post(
    "/{table_id}/questions", response_model=GoldenQuestionRead, status_code=201
)
def create_question(
    table_id: str,
    payload: GoldenQuestionCreate,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    data = payload.model_dump()
    data["table_id"] = table_id
    q = GoldenQuestion(**data)
    session.add(q)
    session.commit()
    session.refresh(q)

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
            raise HTTPException(
                status_code=400, detail="Unsupported file format. Use JSON or Excel."
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error parsing file: {e!s}")

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
            question_type=QuestionType(q_type),
        )
        session.add(q)
        session.flush()  # get ID without commit

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


# ── Connection-Scoped Golden Questions (Jeen Metadata) ─────────────────────────

connection_questions_router = APIRouter(
    prefix="/connections", tags=["connection-golden-questions"]
)


@connection_questions_router.get(
    "/{connection_id}/questions", response_model=list[GoldenQuestionRead]
)
async def list_connection_questions(
    connection_id: int,
    session: Session = Depends(get_session),
):
    """List all golden questions for a catalog connection from Jeen metadata."""
    try:
        jeen_pairs = await fetch_jeen_sql_pairs(connection_id)
        if jeen_pairs:
            # Sync Jeen pairs into DB for FK integrity and caching
            existing_db_questions = session.exec(
                select(GoldenQuestion).where(
                    GoldenQuestion.connection_id == connection_id
                )
            ).all()
            by_question_text = {q.question.strip(): q for q in existing_db_questions}

            results: list[GoldenQuestion] = []
            for item in jeen_pairs:
                q_text = (item.get("question") or "").strip()
                sql = (
                    item.get("sql_statement")
                    or item.get("query")
                    or item.get("expected_sql")
                    or ""
                ).strip()
                if not q_text:
                    continue

                if q_text in by_question_text:
                    q = by_question_text[q_text]
                    if sql and q.expected_sql != sql:
                        q.expected_sql = sql
                        session.add(q)
                    results.append(q)
                else:
                    q = GoldenQuestion(
                        connection_id=connection_id,
                        question=q_text,
                        expected_sql=sql,
                        difficulty=DifficultyLevel.simple,
                        question_type=QuestionType.simple,
                    )
                    session.add(q)
                    results.append(q)

            session.commit()
            for r in results:
                session.refresh(r)
            return results
    except Exception as e:
        logger.warning(
            f"[Questions] Jeen metadata fetch failed for connection {connection_id}: {e}"
        )
        return []

    return []


@connection_questions_router.post(
    "/{connection_id}/questions", response_model=GoldenQuestionRead, status_code=201
)
async def create_connection_question(
    connection_id: int,
    payload: GoldenQuestionCreate,
    session: Session = Depends(get_session),
):
    """Add or upsert a golden question into Jeen metadata and local DB."""
    # 1. Save to Jeen metadata
    diff_val = (
        payload.difficulty.value
        if hasattr(payload.difficulty, "value")
        else str(payload.difficulty)
    )
    type_val = (
        payload.question_type.value
        if hasattr(payload.question_type, "value")
        else str(payload.question_type)
    )

    try:
        await save_jeen_sql_pair(
            connection_id=connection_id,
            question=payload.question,
            query=payload.expected_sql,
            category="General",
            tags=[diff_val, type_val],
        )
    except Exception as e:
        logger.warning(
            f"[Questions] Failed to save question to Jeen metadata for connection {connection_id}: {e}"
        )

    # 2. Upsert to local DB
    existing = session.exec(
        select(GoldenQuestion).where(
            GoldenQuestion.connection_id == connection_id,
            GoldenQuestion.question == payload.question,
        )
    ).first()

    if existing:
        existing.expected_sql = payload.expected_sql
        existing.difficulty = payload.difficulty
        existing.question_type = payload.question_type
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    data = payload.model_dump()
    data["connection_id"] = connection_id
    q = GoldenQuestion(**data)
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


@connection_questions_router.put(
    "/{connection_id}/questions/{question_id}", response_model=GoldenQuestionRead
)
async def update_connection_question(
    connection_id: int,
    question_id: str,
    payload: GoldenQuestionCreate,
    session: Session = Depends(get_session),
):
    """Update a golden question in Jeen metadata and local DB."""
    q = session.get(GoldenQuestion, question_id)
    if not q or q.connection_id != connection_id:
        raise HTTPException(
            status_code=404, detail="Question not found for this connection"
        )

    diff_val = (
        payload.difficulty.value
        if hasattr(payload.difficulty, "value")
        else str(payload.difficulty)
    )
    type_val = (
        payload.question_type.value
        if hasattr(payload.question_type, "value")
        else str(payload.question_type)
    )

    # Save update to Jeen metadata
    try:
        await save_jeen_sql_pair(
            connection_id=connection_id,
            question=payload.question,
            query=payload.expected_sql,
            category="General",
            tags=[diff_val, type_val],
        )
    except Exception as e:
        logger.warning(
            f"[Questions] Failed to update question in Jeen metadata for connection {connection_id}: {e}"
        )

    q.question = payload.question
    q.expected_sql = payload.expected_sql
    q.difficulty = payload.difficulty
    q.question_type = payload.question_type
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


@connection_questions_router.delete(
    "/{connection_id}/questions/{question_id}", status_code=204
)
def delete_connection_question(
    connection_id: int,
    question_id: str,
    session: Session = Depends(get_session),
):
    """Delete a golden question associated with a connection."""
    q = session.get(GoldenQuestion, question_id)
    if not q or q.connection_id != connection_id:
        raise HTTPException(
            status_code=404, detail="Question not found for this connection"
        )
    session.delete(q)
    session.commit()
