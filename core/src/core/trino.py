"""
trino.py — Real Trino connection and execution helper.
"""

import logging
import time
from typing import Any

import trino
from pydantic import BaseModel

from core.config import settings

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


def execute_query_sync(sql: str, table_id: str = "", params: tuple | dict | list | None = None) -> TrinoExecutionResult:
    """
    Execute a SQL query against the real Trino cluster.
    """
    start_time = time.time()
    log_sql = sql.strip().replace("\n", " ")[:300]
    logger.info(f"[CoreTrinoClient] table_id={table_id} | {log_sql}")

    if not settings.TRINO_ENABLED:
        logger.warning("[CoreTrinoClient] TRINO_ENABLED=False — skipping real query")
        return TrinoExecutionResult(
            success=False,
            error_message="Trino disabled (TRINO_ENABLED=False)",
            execution_time_ms=0,
        )

    conn = None
    cur = None
    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[CoreTrinoClient] OK — {len(rows)} rows in {execution_time_ms}ms")
        cur.close()
        conn.close()
        return TrinoExecutionResult(
            success=True,
            rows=[list(r) for r in rows],
            columns=columns,
            row_count=len(rows),
            execution_time_ms=execution_time_ms,
        )
    except Exception as exc:
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"[CoreTrinoClient] FAILED in {execution_time_ms}ms: {exc}")
        if cur is not None:
            try:
                cur.close()
            except:
                pass
        if conn is not None:
            try:
                conn.close()
            except:
                pass
        return TrinoExecutionResult(
            success=False,
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=execution_time_ms,
            error_message=str(exc),
        )
