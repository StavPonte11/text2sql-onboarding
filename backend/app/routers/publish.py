from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    Table, TableStatus, EvalRun, EnrichmentVersion, GoldenQuestion
)

router = APIRouter(prefix="/tables", tags=["publish"])


@router.post("/{table_id}/publish")
def publish_table(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    errors = []

    # Check enrichment exists
    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    if not enrichment:
        errors.append({"code": "MISSING_ENRICHMENT", "message": "Table has no enrichment"})

    # Check golden questions
    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if len(questions) < 1:
        errors.append({"code": "MISSING_QUESTIONS", "message": "At least 1 golden question required"})

    # Check latest eval run
    latest_run = session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id)
        .order_by(EvalRun.created_at.desc())
    ).first()
    if not latest_run:
        errors.append({"code": "MISSING_EVAL", "message": "No evaluation run found"})
    elif latest_run.score < 0.7:
        errors.append({
            "code": "LOW_EVAL_SCORE",
            "message": f"Eval score {latest_run.score:.0%} is below the 70% threshold",
        })

    if errors:
        raise HTTPException(status_code=422, detail={"blocking_errors": errors})

    table.status = TableStatus.production
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()
    session.refresh(table)
    return {"status": "published", "table_id": table.id}
