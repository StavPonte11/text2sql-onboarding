"""
Read-only evaluation results for scripts outside the app.

The app's own endpoints need an SSO session; these take
`Authorization: Bearer <EVAL_EXPORT_API_KEY>` instead, and are off while
that key isn't set.
"""

import hmac

from core.db.engine import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session

from app.config import settings
from app.routers.orchestration import export_run_results, list_runs


def require_export_key(request: Request) -> None:
    expected = settings.EVAL_EXPORT_API_KEY
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="The export API is off: set EVAL_EXPORT_API_KEY on the backend.",
        )
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing export API key")


router = APIRouter(
    prefix="/export/evaluations",
    tags=["export"],
    dependencies=[Depends(require_export_key)],
)


@router.get("/runs")
def export_list_runs(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    status: str | None = Query(default=None),
    connection_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Evaluation runs, newest first (same as GET /api/evaluations/runs)."""
    return list_runs(
        limit=limit,
        offset=offset,
        status=status,
        table_id=None,
        connection_id=connection_id,
        session=session,
    )


@router.get("/runs/{run_id}/results")
def export_results(
    run_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_session),
):
    """A run's questions with golden/generated SQL and their rows, a page at a time."""
    return export_run_results(run_id, offset=offset, limit=limit, session=session)
