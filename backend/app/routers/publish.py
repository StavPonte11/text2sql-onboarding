"""
publish.py — Table publish workflow with regression gate.

Publish is BLOCKED if:
1. No enrichment
2. No golden questions
3. No completed eval run
4. Eval score < BLOCK_THRESHOLD (0.80)
5. Any production table's score drops > REGRESSION_BLOCK (0.10) after re-eval (optional strict mode)
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import (
    Table, TableStatus, EvalRun, EvalStatus,
    EnrichmentVersion, GoldenQuestion,
)
from app.services.scoring import BLOCK_THRESHOLD, REGRESSION_BLOCK, PASS_THRESHOLD

router = APIRouter(prefix="/tables", tags=["publish"])


def _check_regression(table_id: str, new_score: float, session: Session) -> list[dict]:
    """
    Check if publishing this table causes a regression in any production table.
    Returns list of blocking regression errors.
    In production: re-run ALL production tables' eval runs and compare scores.
    Here: we check if the new table's own historical score dropped significantly.
    """
    warnings = []

    # Get the previous completed run for this table
    all_runs = session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id, EvalRun.status == EvalStatus.completed)
        .order_by(EvalRun.created_at.desc())
        .limit(2)
    ).all()

    if len(all_runs) >= 2:
        prev_score = all_runs[1].score
        delta = prev_score - new_score
        if delta > REGRESSION_BLOCK:
            warnings.append({
                "code": "REGRESSION_BLOCK",
                "message": f"Score dropped {delta:.0%} from previous run ({prev_score:.0%} → {new_score:.0%}). Threshold: {REGRESSION_BLOCK:.0%}",
                "severity": "blocking",
                "prev_score": prev_score,
                "new_score": new_score,
                "delta": delta,
            })
        elif delta > 0.05:
            warnings.append({
                "code": "REGRESSION_WARNING",
                "message": f"Score dropped {delta:.0%} — recommend review before publishing.",
                "severity": "warning",
                "prev_score": prev_score,
                "new_score": new_score,
                "delta": delta,
            })

    return warnings


@router.post("/{table_id}/publish")
def publish_table(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    blocking_errors = []
    warnings = []

    # ── Gate 1: Enrichment ────────────────────────────────────────────────────
    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    if not enrichment:
        blocking_errors.append({
            "code": "MISSING_ENRICHMENT",
            "message": "Table must have semantic enrichment before publishing.",
        })

    # ── Gate 2: Golden Questions ───────────────────────────────────────────────
    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if len(questions) < 3:
        blocking_errors.append({
            "code": "INSUFFICIENT_QUESTIONS",
            "message": f"At least 3 golden questions required. Found: {len(questions)}.",
        })

    # ── Gate 3: Eval run must exist and be completed ───────────────────────────
    latest_run = session.exec(
        select(EvalRun)
        .where(EvalRun.table_id == table_id, EvalRun.status == EvalStatus.completed)
        .order_by(EvalRun.created_at.desc())
    ).first()

    if not latest_run:
        blocking_errors.append({
            "code": "MISSING_EVAL",
            "message": "No completed evaluation run found. Run an evaluation first.",
        })
    else:
        # ── Gate 4: Score threshold ────────────────────────────────────────────
        if latest_run.score < BLOCK_THRESHOLD:
            blocking_errors.append({
                "code": "LOW_EVAL_SCORE",
                "message": f"Eval score {latest_run.score:.0%} is below the required {BLOCK_THRESHOLD:.0%} threshold.",
                "score": latest_run.score,
                "required": BLOCK_THRESHOLD,
            })

        # ── Gate 5: Regression check ───────────────────────────────────────────
        if not blocking_errors:  # Only check regression if other gates pass
            regression_results = _check_regression(table_id, latest_run.score, session)
            for r in regression_results:
                if r["severity"] == "blocking":
                    blocking_errors.append(r)
                else:
                    warnings.append(r)

    if blocking_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "blocking_errors": blocking_errors,
                "warnings": warnings,
            }
        )

    # ── Publish ────────────────────────────────────────────────────────────────
    table.status = TableStatus.production
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()
    session.refresh(table)

    return {
        "status": "published",
        "table_id": table.id,
        "eval_score": latest_run.score if latest_run else None,
        "warnings": warnings,
    }
