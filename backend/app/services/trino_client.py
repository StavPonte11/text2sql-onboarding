"""
trino_client.py — Real Trino DBAPI execution client.

Connects to the production Trino cluster using the trino-python-client.
Falls back gracefully with a structured error if the cluster is unreachable.
All queries are logged with execution time for observability.
"""

import logging
import time
from typing import Any

import trino
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class TrinoExecutionResult(BaseModel):
    success: bool
    rows: list[list[Any]] = []
    columns: list[str] = []
    row_count: int = 0
    execution_time_ms: int = 0
    error_message: str | None = None


def get_trino_connection():
    """Create a real Trino DBAPI connection from settings."""
    auth = None
    if settings.TRINO_CERT_PATH and settings.TRINO_KEY_PATH:
        auth = trino.auth.CertificateAuthentication(
            settings.TRINO_CERT_PATH, settings.TRINO_KEY_PATH
        )
    elif settings.TRINO_PASSWORD:
        auth = trino.auth.BasicAuthentication(
            settings.TRINO_USER, settings.TRINO_PASSWORD
        )

    return trino.dbapi.connect(
        host=settings.TRINO_HOST,
        port=settings.TRINO_PORT,
        user=settings.TRINO_USER,
        catalog=settings.TRINO_CATALOG,
        schema=settings.TRINO_SCHEMA,
        http_scheme=settings.TRINO_HTTP_SCHEME,
        auth=auth,
        request_timeout=settings.TRINO_REQUEST_TIMEOUT,
        verify=settings.TRINO_VERIFY,
    )


def execute_query_sync(sql: str, table_id: str = "") -> TrinoExecutionResult:
    """
    Execute a SQL query against the real Trino cluster.

    Returns a structured TrinoExecutionResult with rows, columns, and timing.
    Never raises — all errors are captured in error_message.
    """
    start_time = time.time()
    log_sql = sql.strip().replace("\n", " ")[:300]
    logger.info(f"[TrinoClient] table_id={table_id} | {log_sql}")

    if not settings.TRINO_ENABLED:
        logger.warning("[TrinoClient] TRINO_ENABLED=False — skipping real query")
        return TrinoExecutionResult(
            success=False,
            error_message="Trino disabled (TRINO_ENABLED=False)",
            execution_time_ms=0,
        )

    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[TrinoClient] OK — {len(rows)} rows in {execution_time_ms}ms")
        return TrinoExecutionResult(
            success=True,
            rows=[list(r) for r in rows],
            columns=columns,
            row_count=len(rows),
            execution_time_ms=execution_time_ms,
        )
    except Exception as exc:
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"[TrinoClient] FAILED in {execution_time_ms}ms: {exc}")
        return TrinoExecutionResult(
            success=False,
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=execution_time_ms,
            error_message=str(exc),
        )
