"""
SQL tools: run read-only queries on Trino, sample a table, list a column's distinct values, and
dry-run a query (validate_sql).

Every statement goes through agent.utils.sql_guard (one read-only statement, row ceiling, safe
identifiers). Failures carry Trino's own error unchanged (deep_agent.tools.trino_errors). A
successful execute_trino_query is recorded in state so the final answer can be bound to it
(deep_agent.answer.verification).
"""

import logging

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

import deep_agent.tools.trino_errors as trino_errors
from agent.config import settings
from agent.utils import sql_guard
from agent.utils.sql import clean_sql
from agent.utils.sql_guard import effective_cap
from core.trino import execute_query_sync
from deep_agent.answer.verification import executed_query_record
from deep_agent.context import Runtime
from deep_agent.observability import annotate_current_span
from deep_agent.tools.envelope import fail, ok

logger = logging.getLogger(__name__)


def _run_sql(sql: str, row_cap: int | None, requested: int | None = None) -> str:
    """Run guarded SQL; when the requested row count was reduced to the configured ceiling, say so."""
    content, payload = _execute(sql, row_cap)
    if payload is None or requested is None or row_cap is None or requested <= row_cap:
        return content
    return ok({**payload, "notice": f"limit reduced from {requested} to {row_cap} (the configured ceiling) — "
                                    "asking for more returns the same rows"})


def _execute(sql: str, row_cap: int | None) -> tuple[str, dict | None]:
    """Execute already-guarded SQL that fetches up to row_cap + 1 rows; report truncation.

    Returns (envelope, success payload or None).
    row_cap=None means no ceiling: every returned row is passed through.
    Telemetry (query id, timing, row count / Trino error) goes both into the envelope
    and onto the current Langfuse tool observation.
    """
    try:
        res = execute_query_sync(sql, reuse_connection=True)
    except Exception as e:
        err = trino_errors.from_exception(e)
        annotate_current_span({"error_name": err["name"]}, level="ERROR")
        return fail(err), None
    if not res.success:
        err = trino_errors.from_result(res)
        err["execution_time_ms"] = res.execution_time_ms
        annotate_current_span({
            "trino_query_id": res.query_id,
            "execution_time_ms": res.execution_time_ms,
            "error_name": err["name"],
            "error_type": err.get("type"),
        }, level="ERROR")
        return fail(err), None

    rows = res.rows or []
    truncated = row_cap is not None and len(rows) > row_cap
    if truncated:
        rows = rows[:row_cap]
    annotate_current_span({
        "trino_query_id": res.query_id,
        "execution_time_ms": res.execution_time_ms,
        "row_count": len(rows),
        "truncated": truncated,
    })
    payload = {
        "columns": res.columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows,
        "query_id": res.query_id,
        "execution_time_ms": res.execution_time_ms,
    }
    return ok(payload), payload


@tool
def execute_trino_query(sql: str, limit: int | None = None, runtime: Runtime = None) -> str | Command:
    """
    Executes a read-only Trino SQL query and returns the resulting rows.

    Use this to test SQL queries against the actual database and perform exploratory data analysis.
    Only a single SELECT / WITH (or SHOW / DESCRIBE) statement is accepted; anything that writes,
    multiple statements, SQL comments and TABLE(...) pass-through functions are rejected with
    error.name FORBIDDEN_STATEMENT.

    Results are capped at a configured row ceiling (when one is set): an outer LIMIT is enforced
    automatically, and data.truncated is true when more rows existed than were returned.

    On failure, error carries Trino's own error, unmodified:
    - error.name: Trino's error code (e.g. SYNTAX_ERROR, COLUMN_NOT_FOUND, TYPE_MISMATCH,
      EXPRESSION_NOT_AGGREGATE, DIVISION_BY_ZERO)
    - error.type: Trino's error class — USER_ERROR means the SQL itself is wrong;
      INSUFFICIENT_RESOURCES, INTERNAL_ERROR and EXTERNAL come from the cluster or the source database
    - error.message: Trino's message; error.location: {line, column} of the offending token
    Two errors don't come from Trino: FORBIDDEN_STATEMENT (rejected before reaching Trino — rewrite
    as a single read-only query without comments) and CONNECTION_ERROR (Trino could not be reached).

    Args:
        sql (str): The exact Trino SQL query string to execute. Markdown fences and trailing
                   semicolons are stripped automatically.
        limit (int, optional): Maximum number of rows to return. Defaults to the configured row
                   ceiling; a larger value is reduced to that ceiling.
    """
    row_cap = effective_cap(limit, settings.DEEP_AGENT_MAX_ROWS)
    try:
        guarded = sql_guard.prepare(clean_sql(sql), row_cap)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    content, payload = _execute(guarded.sql, guarded.row_cap if guarded.is_query else settings.DEEP_AGENT_MAX_ROWS)
    if payload is None or not guarded.is_query or runtime is None or not runtime.tool_call_id:
        return content
    # Record the successful execution so the final answer can be bound to it (deep_agent.answer.verification).
    return Command(update={
        "executed_queries": [executed_query_record(sql, guarded.sql, payload, row_cap)],
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name="execute_trino_query")],
    })


@tool
def get_sample_rows(table_name: str, limit: int = 5) -> str:
    """
    Retrieves a small number of raw data rows from a table using a live Trino query.

    Use this only when schema profiles don't provide enough context and you must see exact raw
    data formats (e.g., date formatting, nested JSON structure, exact string casing).
    Do NOT use this for counting or aggregating data.

    Args:
        table_name (str): The fully qualified exact name of the table, e.g. '"catalog"."schema"."table"'.
        limit (int): The number of rows to sample. Defaults to 5 (bounded by the configured sample ceiling).
    """
    requested = limit if limit is not None else 5
    row_cap = effective_cap(requested, settings.DEEP_AGENT_MAX_SAMPLE_ROWS)
    try:
        sql = sql_guard.sample_rows_sql(table_name, row_cap)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    return _run_sql(sql, row_cap, requested)


@tool
def get_column_distinct_values(table_name: str, column_name: str, limit: int = 50) -> str:
    """
    Executes a live Trino query to fetch unique values for a specific column.
    Use this to see exact existing strings in the database before writing an exact-match WHERE clause.

    BEST USED FOR:
    - Low-cardinality `categorical` columns (e.g. status, priority, category) where you want to see all
      5-20 possible valid values, and the catalog doesn't already list all of them.

    DO NOT USE FOR:
    - High-cardinality columns (`large_categorical`, `large_unit_id`, names, SKUs, cities). Dumping 50-100
      random values from a table with thousands of entities wastes tokens and latency without finding the
      specific entity you need. Use `search_column_values` instead for targeted entity search.
    - Numeric, date, or continuous columns (checking unique values is meaningless for value matching).
    - Columns whose values the catalog already lists in full ("values (all)").

    Args:
        table_name (str): The fully qualified exact name of the table.
        column_name (str): The exact name of the column (plain or double-quoted; spaces and Hebrew are fine).
        limit (int): Maximum distinct values to return. Defaults to 50 (bounded by the configured row ceiling).
    """
    requested = limit if limit is not None else 50
    row_cap = effective_cap(requested, settings.DEEP_AGENT_MAX_ROWS)
    try:
        sql = sql_guard.distinct_values_sql(table_name, column_name, row_cap)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    return _run_sql(sql, row_cap, requested)


_VALIDATE_PREFIX = "EXPLAIN (TYPE VALIDATE) "


@tool
def validate_sql(sql: str) -> str:
    """
    Asks Trino to check a query without running it (EXPLAIN (TYPE VALIDATE)). Trino parses it and
    resolves it against the catalog: syntax, table and column names, types and function signatures.
    No data is read.

    Optional. For an invalid query, execute_trino_query fails at the same checking step, just as
    fast and without reading data, with the same error fields — so validating first usually
    saves nothing. Use it only to check a draft you do not want to run yet. It cannot tell you
    whether the query returns rows.

    Returns ok=true with data.valid=true, or ok=false with the same error fields as
    execute_trino_query (error.name, error.type, error.message, error.location).

    Args:
        sql (str): The exact Trino SQL query string to validate.
    """
    sql_clean = clean_sql(sql)
    try:
        sql_guard.parse_read_statement(sql_clean)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    try:
        res = execute_query_sync(f"{_VALIDATE_PREFIX}{sql_clean}", reuse_connection=True)
    except Exception as e:
        return fail(trino_errors.from_exception(e))
    if res.success:
        return ok({"valid": True})
    err = trino_errors.from_result(res)
    location = err.get("location")
    if location and location["line"] == 1:
        # Trino counts columns from the start of "EXPLAIN (TYPE VALIDATE) ..."; report them against the caller's SQL.
        location["column"] = max(1, location["column"] - len(_VALIDATE_PREFIX))
    return fail(err)
