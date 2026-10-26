"""
trino.py — Real Trino connection and execution helper.
"""

import logging
import threading
import time
from typing import Any

import trino
import trino.exceptions
from pydantic import BaseModel
import urllib3
from core.config import settings

if not settings.TRINO_VERIFY:
    # Suppress unverified HTTPS warnings for dev internal endpoints
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Per-thread reusable connections for callers that opt in (execute_query_sync(reuse_connection=True)).
_thread_local = threading.local()


def _reusable_connection():
    conn = getattr(_thread_local, "connection", None)
    if conn is None:
        conn = get_trino_connection()
        _thread_local.connection = conn
    return conn


def _drop_reusable_connection() -> None:
    conn = getattr(_thread_local, "connection", None)
    _thread_local.connection = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


class TrinoExecutionResult(BaseModel):
    success: bool
    rows: list[list[Any]] = []
    columns: list[str] = []
    row_count: int = 0
    execution_time_ms: int = 0
    error_message: str | None = None
    # Structured Trino error details (populated from trino.exceptions.TrinoQueryError)
    error_name: str | None = None        # e.g. SYNTAX_ERROR, COLUMN_NOT_FOUND, FUNCTION_NOT_FOUND
    error_type: str | None = None        # USER_ERROR | INSUFFICIENT_RESOURCES | INTERNAL_ERROR | EXTERNAL
    error_code: int | None = None
    error_location: tuple[int, int] | None = None  # (line, column) of the offending token
    query_id: str | None = None


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


def execute_query_sync(
    sql: str,
    table_id: str = "",
    params: tuple | dict | list | None = None,
    reuse_connection: bool = False,
) -> TrinoExecutionResult:
    """
    Execute a SQL query against the real Trino cluster.

    reuse_connection=True keeps one DBAPI connection per thread (no new HTTP/TLS setup per
    query); a failed query drops it so the next call reconnects.
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
        conn = _reusable_connection() if reuse_connection else get_trino_connection()
        cur = conn.cursor()
        if params is not None:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        query_id = getattr(cur, "query_id", None)
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[CoreTrinoClient] OK — {len(rows)} rows in {execution_time_ms}ms")
        cur.close()
        if not reuse_connection:
            conn.close()
        return TrinoExecutionResult(
            success=True,
            rows=[list(r) for r in rows],
            columns=columns,
            row_count=len(rows),
            execution_time_ms=execution_time_ms,
            query_id=query_id,
        )
    except Exception as exc:
        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"[CoreTrinoClient] FAILED in {execution_time_ms}ms: {exc}")
        if cur is not None:
            try:
                cur.close()
            except:
                pass
        if reuse_connection:
            _drop_reusable_connection()
        elif conn is not None:
            try:
                conn.close()
            except:
                pass
        details: dict[str, Any] = {}
        if isinstance(exc, trino.exceptions.TrinoQueryError):
            details = {
                "error_message": exc.message,
                "error_name": exc.error_name,
                "error_type": exc.error_type,
                "error_code": exc.error_code,
                "error_location": exc.error_location,
                "query_id": exc.query_id,
            }
        return TrinoExecutionResult(
            success=False,
            rows=[],
            columns=[],
            row_count=0,
            execution_time_ms=execution_time_ms,
            **{"error_message": str(exc), **details},
        )
