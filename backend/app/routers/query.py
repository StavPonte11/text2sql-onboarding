import time
from typing import Any

from core.trino import get_trino_connection
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/query", tags=["query"])


class QueryRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    success: bool
    rows: list[list[Any]]
    columns: list[str]
    row_count: int
    execution_time_ms: float
    error: str | None


@router.post("/execute", response_model=QueryResponse)
def execute_query(request: QueryRequest) -> QueryResponse:
    """
    Execute a SQL query against Trino and return the results.
    """
    start_time = time.time()

    if not settings.TRINO_ENABLED:
        return QueryResponse(
            success=True,
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=0.0,
            error="Trino execution is disabled (TRINO_ENABLED=False)",
        )

    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        cur.execute(request.sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        execution_time_ms = (time.time() - start_time) * 1000

        cur.close()
        conn.close()

        return QueryResponse(
            success=True,
            rows=[list(row) for row in rows],
            columns=columns,
            row_count=len(rows),
            execution_time_ms=execution_time_ms,
            error=None,
        )
    except Exception as exc:
        execution_time_ms = (time.time() - start_time) * 1000
        return QueryResponse(
            success=False,
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=execution_time_ms,
            error=str(exc),
        )
