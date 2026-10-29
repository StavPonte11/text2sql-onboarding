"""
Deep Agent tools.

Every tool returns the same JSON envelope (see deep_agent.envelope):
    {"ok": true, "data": ..., "error": null} | {"ok": false, "data": null, "error": {"name", "message", ...}}

The connection's catalog is not a tool: it is loaded for every run and shown to the model by
CatalogMiddleware (deep_agent.catalog). DEEP_AGENT_TOOLS (bottom of this file) is what the model
gets; a few metadata tools are kept here unregistered because the catalog already contains
their information.
"""

import logging
from typing import Any, Optional

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from agent.config import settings
from agent.services.geo_utils import geojson_to_simplified_wkt, get_geojson_polygon
from agent.utils import sql_guard
from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from agent.utils.sql import clean_sql
from agent.utils.sql_guard import effective_cap
from core.trino import execute_query_sync
from deep_agent import trino_errors
from deep_agent.answer import executed_query_record
from deep_agent.context import DeepAgentContext, cached_catalog, connection_id, user_request
from deep_agent.envelope import (
    ENRICHMENT_ERROR,
    LOCATION_NOT_FOUND,
    METADATA_ERROR,
    NOT_FOUND,
    error,
    fail,
    ok,
)
from deep_agent.observability import annotate_current_span

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metadata (beyond the catalog)
# ---------------------------------------------------------------------------

Runtime = ToolRuntime[DeepAgentContext]


def _metadata_failure(what: str, exc: Exception) -> str:
    logger.warning("%s failed: %s", what, exc)
    return fail(error(
        METADATA_ERROR,
        f"{what} failed: {exc}. The metadata service failed — this does not mean the data is empty.",
    ))


@tool
async def get_database_tables(runtime: Runtime = None) -> str:
    """
    Returns the list of all tables in the connection with their business descriptions and
    exact quoted identifiers ("catalog"."schema"."table") for querying.
    Use this for a quick, clean roster of available tables.
    """
    try:
        return ok(await get_jeen_metadata_client().get_tables(connection_id=connection_id(runtime)))
    except Exception as e:
        return _metadata_failure("get_database_tables", e)


@tool
async def get_table_columns(table_name: str, runtime: Runtime = None) -> str:
    """
    Returns the columns for a specific table: data type, primary key flag, business description,
    real example values, and profiling statistics.

    Args:
        table_name (str): The table identifier (e.g. '"catalog"."schema"."orders"' or 'orders').
    """
    try:
        return ok(await get_jeen_metadata_client().get_columns(table_name, connection_id=connection_id(runtime)))
    except Exception as e:
        return _metadata_failure(f"get_table_columns({table_name})", e)


@tool
async def get_table_relationships(runtime: Runtime = None) -> str:
    """
    Returns the source-scoped foreign-key and semantic join relationships between tables.
    Use this to identify exact join paths and foreign key pairs.
    """
    try:
        rels = await get_jeen_metadata_client().get_relationships(connection_id=connection_id(runtime))
        return ok({"relationships": rels})
    except Exception as e:
        return _metadata_failure("get_table_relationships", e)


@tool
async def get_table_profile(table_name: str, runtime: Runtime = None) -> str:
    """
    Retrieves the latest stored schema and statistics for one specific table.

    Use this to understand a single table's row counts, column roles, null ratios, distinct counts,
    and min/max values before writing SQL against it.
    Do NOT use this to fetch raw data rows; use `get_sample_rows` or `execute_trino_query` instead.

    Args:
        table_name (str): The fully qualified, exact 3-part name of the table,
                          e.g., '"catalog"."schema"."table"'.
    """
    try:
        profile = await get_jeen_metadata_client().get_table_profile(table_name, connection_id=connection_id(runtime))
    except Exception as e:
        return _metadata_failure(f"get_table_profile({table_name})", e)
    if not profile:
        return fail(error(NOT_FOUND, f"No stored profile for table {table_name}"))
    return ok(profile)


@tool
async def get_column_profile(table_name: str, column_name: str, runtime: Runtime = None) -> str:
    """
    Retrieves the full stored statistics of one column — detail the catalog does not show:
    null count / ratio, top values with their frequencies, string lengths, quartiles and a histogram.

    The catalog already lists each column's type, semantic_type, distinct count, range and example
    values (all values when the domain is complete), so call this only when you need that extra
    detail — e.g. checking NULLs when a query returns 0 rows, or a value distribution.

    Args:
        table_name (str): The fully qualified exact name of the table, e.g., '"catalog"."schema"."table"'.
        column_name (str): The exact name of the column to profile.
    """
    try:
        profile = await get_jeen_metadata_client().get_column_profile(
            table_name, column_name, connection_id=connection_id(runtime)
        )
    except Exception as e:
        return _metadata_failure(f"get_column_profile({table_name}.{column_name})", e)
    if not profile:
        return fail(error(NOT_FOUND, f"No stored profile for column {table_name}.{column_name}"))
    return ok(profile)


# ---------------------------------------------------------------------------
# Query Execution
# ---------------------------------------------------------------------------

def _run_sql(sql: str, row_cap: int | None) -> str:
    return _execute(sql, row_cap)[0]


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
    # Record the successful execution so the final answer can be bound to it (deep_agent.answer).
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
    row_cap = effective_cap(limit if limit is not None else 5, settings.DEEP_AGENT_MAX_SAMPLE_ROWS)
    try:
        sql = sql_guard.sample_rows_sql(table_name, row_cap)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    return _run_sql(sql, row_cap)


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
    row_cap = effective_cap(limit if limit is not None else 50, settings.DEEP_AGENT_MAX_ROWS)
    try:
        sql = sql_guard.distinct_values_sql(table_name, column_name, row_cap)
    except sql_guard.GuardError as e:
        return fail(trino_errors.forbidden(str(e)))
    return _run_sql(sql, row_cap)


# ---------------------------------------------------------------------------
# SQL Quality
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Metadata Search
# ---------------------------------------------------------------------------

@tool
async def search_metadata(query: str, limit: int = 10, runtime: Runtime = None) -> str:
    """
    Searches the metadata catalog across all tables, columns, relationships, and business terms.

    Use this to quickly discover relevant schema or glossary context when you aren't sure which
    table to look at. Works well with Hebrew or English terms.
    Do NOT use this to search for specific data row values (e.g., 'John Doe'); use
    `search_column_values` instead.

    Args:
        query (str): The search text (multilingual/Hebrew support) to match against metadata.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    try:
        return ok(await get_jeen_metadata_client().search(query, limit, connection_id=connection_id(runtime)))
    except Exception as e:
        return _metadata_failure("search_metadata", e)


@tool
async def search_business_terms(query: str, limit: int = 10, runtime: Runtime = None) -> str:
    """
    Searches the business glossary for terms relevant to a natural language question.

    Use this to bridge the gap between user vocabulary and database terminology
    (e.g., matching 'מכירות' to the 'Revenue' column). Ranked by semantic meaning, not keywords.
    Do NOT use this to search for database tables or column names directly.

    Args:
        query (str): A natural language question or phrase (multilingual support).
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    try:
        terms = await get_jeen_metadata_client().search_business_terms(query, limit, connection_id=connection_id(runtime))
        return ok(terms)
    except Exception as e:
        return _metadata_failure("search_business_terms", e)


@tool
async def search_knowledge_pairs(query: str, limit: int = 10, runtime: Runtime = None) -> str:
    """
    Searches saved question-and-query examples that are semantically similar to the user's question.

    Use this to find few-shot examples of how similar questions were solved previously. Even if you
    think you know how to write the query, similar past examples often reveal domain-specific SQL
    conventions, exact JOIN paths, or edge cases you haven't considered.

    Pass the raw Hebrew natural language question exactly as the user wrote it — never SQL keywords.
    Only the stored question is matched (never the query text), so full sentences work best.

    Args:
        query (str): The natural language user question (multilingual/Hebrew support).
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    try:
        pairs = await get_jeen_metadata_client().search_knowledge_pairs(query, limit, connection_id=connection_id(runtime))
        return ok(pairs)
    except Exception as e:
        return _metadata_failure("search_knowledge_pairs", e)


def _round_or_none(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _value_match(v: Any) -> dict:
    if not isinstance(v, dict):
        return {"value": v}
    return {
        "value": v.get("value"),
        "table": v.get("table_name") or v.get("table"),
        "column": v.get("column_name") or v.get("column"),
        "keyword_similarity": _round_or_none(v.get("keyword_similarity")),
        "semantic_distance": _round_or_none(v.get("semantic_distance")),
    }


@tool
async def search_column_values(
    search_term: str,
    table: Optional[str] = None,
    column: Optional[str] = None,
    limit: int = 10,
    runtime: Runtime = None,
) -> str:
    """
    Searches for exact real values contained within indexed columns across the database.

    SUPPORTED COLUMN TYPES:
    Works exclusively on high-cardinality columns profiled as:
    - `large_categorical`
    - `large_unit_id`
    ⚠️  Do NOT use on standard `categorical` or `boolean` columns (e.g. a `status` column) — they are
    not indexed in vector search and will return 0 matches! Use `get_column_distinct_values` or the catalog instead.
    Do NOT use on numeric, date/timestamp, or geometry columns.

    WHEN TO USE:
    - Use whenever you need to find how a high-cardinality entity is represented or spelled in the database
      (e.g., finding 'Acme Corporation LLC' from 'Acme Corp', or the stored spelling of a Hebrew term). Multilingual,
      keyword, and vector search finds the exact database spelling.

    Returns data.matches — every matching value (use each 'value' verbatim in SQL) with:
    - 'keyword_similarity' close to 1.0 = strong lexical match
    - 'semantic_distance' close to 0.0 = strong semantic match

    Args:
        search_term (str): The specific value to look for (partial or full string, Hebrew or English).
        table (str, optional): Restrict search to one table name. Omit to search all tables.
        column (str, optional): Restrict search to one column name. Omit to search all columns.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    try:
        res = await get_jeen_metadata_client().search_column_values(
            search_term=search_term, table=table, column=column, limit=limit, raw=True,
            connection_id=connection_id(runtime),
        )
    except Exception as e:
        return _metadata_failure("search_column_values", e)

    values = (res.get("values") or []) if isinstance(res, dict) else []
    matches = [_value_match(v) for v in values]
    return ok({
        "search_term": search_term,
        "matches": matches,
        "notice": None if matches else "No matches — the value may not exist in this column, or the column is not indexed (only large_categorical / large_unit_id are).",
    })


# ---------------------------------------------------------------------------
# Category Enrichment (Automatic Filter Value Correction)
# ---------------------------------------------------------------------------

@tool
async def enrich_sql_filters(sql: str, runtime: Runtime = None) -> str | Command:
    """
    Automatically corrects categorical filter values in a draft SQL query to match exact
    database spellings using semantic search and LLM-guided AST transformation.

    Use this AFTER you have written a draft SQL query that contains WHERE clause filters on
    categorical (text) columns. It will:
    1. Parse the SQL AST to find comparison filters
    2. Semantically search the database for canonical matching values
    3. Use an LLM to select the best match
    4. Rewrite the SQL with exact canonical values (AST-level, not string replacement)

    The catalog and the user's question are taken from the conversation automatically —
    pass only the SQL.

    This is especially powerful for Hebrew→English column value resolution and large categorical
    columns where manually calling search_column_values for every filter would be tedious.

    Do NOT use this on purely numeric SQL or when you already have exact confirmed values.

    Returns data.sql (the enriched SQL, or the original if nothing changed), data.enriched and
    data.changes (one entry per corrected filter).

    Args:
        sql (str): The draft SQL query to enrich.
    """
    from agent.nodes.refiner import parse_jeen_catalog_tables
    from agent.services.enrichment_orchestrator import EnrichmentOrchestrator

    sql_clean = clean_sql(sql)

    catalog = cached_catalog(runtime)
    fetched_catalog = False
    if not catalog:
        try:
            catalog = await get_jeen_metadata_client().get_catalog_prompt(connection_id=connection_id(runtime))
        except Exception as e:
            return _metadata_failure("enrich_sql_filters (catalog fetch)", e)
        fetched_catalog = True

    try:
        schema, tables = parse_jeen_catalog_tables(catalog)
        if not tables:
            return fail(error(ENRICHMENT_ERROR, "No tables could be parsed from the connection catalog. The SQL is unchanged.",
                              original_sql=sql_clean))

        refined_sql, plan, enriched = await EnrichmentOrchestrator.enrich_query(
            user_request=user_request(runtime),
            initial_sql=sql_clean,
            schema=schema,
            tables=tables,
            config=runtime.config if runtime is not None else None,
        )
    except Exception as e:
        logger.error("enrich_sql_filters failed: %s", e, exc_info=True)
        return fail(error(ENRICHMENT_ERROR, f"{e}. The SQL is unchanged.", original_sql=sql_clean))

    changes = []
    if enriched and plan:
        changes = [
            {
                "column": tf.column,
                "original_value": tf.original_value,
                "refined_values": tf.refined_values,
                "old_operator": tf.old_operator,
                "new_operator": tf.new_operator,
            }
            for tf in plan.enrichment_details
            if tf.changed_filter
        ]
    is_enriched = bool(enriched and plan)
    content = ok({"enriched": is_enriched, "sql": refined_sql if is_enriched else sql_clean, "changes": changes})

    if fetched_catalog and runtime is not None and runtime.tool_call_id:
        # Cache the catalog we had to fetch so the next call in this thread doesn't refetch it.
        return Command(update={
            "jeen_catalog": catalog,
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name="enrich_sql_filters")],
        })
    return content


# ---------------------------------------------------------------------------
# Geospatial
# ---------------------------------------------------------------------------

@tool
def resolve_location_to_wkt(location_name: str) -> str:
    """
    Resolves a geographic location name to its WKT (Well-Known Text) polygon representation.

    Use this whenever the user's query involves a geographic area and you need a WKT polygon for
    a Trino ST_GeometryFromText() / ST_Within() / ST_Contains() call.

    Supports Hebrew location names directly (e.g., 'תל אביב', 'ירושלים', 'חיפה').
    Returns data.wkt — the polygon as plain (unquoted) WKT, e.g. POLYGON((...)). In SQL, wrap it
    in single quotes: ST_GeometryFromText('<data.wkt>').

    The function uses rate-limited Nominatim geocoding with caching and automatic WKT
    simplification (binary search). Only area geometries are returned (never points).

    Do NOT use this for exact addresses (e.g., "Rothschild 10, Tel Aviv"); it is designed for
    administrative areas: cities, regions, districts, and countries.

    Args:
        location_name (str): The geographic location name to resolve. Hebrew names are
                             accepted and preferred for Israeli locations.
    """
    geojson = get_geojson_polygon(location_name)
    quoted = geojson_to_simplified_wkt(geojson) if geojson else None  # returns a single-quoted literal
    if not quoted:
        return fail(error(LOCATION_NOT_FOUND, f"Could not resolve location '{location_name}' to an area polygon."))
    return ok({"location": location_name, "wkt": quoted.strip("'")})


# The tools the model gets (order = order shown to the model). The catalog itself is loaded for
# every run by CatalogMiddleware, so there is no catalog tool.
DEEP_AGENT_TOOLS = [
    # Values & places
    search_column_values,
    get_column_distinct_values,
    get_column_profile,
    resolve_location_to_wkt,
    # Query execution
    execute_trino_query,
    get_sample_rows,
    validate_sql,
    # Post-processing
    enrich_sql_filters,
]

# Implemented but not registered: the catalog already contains their information (tables, columns,
# profile stats, relationships, business terms, knowledge pairs). Register again if a connection's
# catalog grows too large to load whole, or when targeted search over those sections pays off.
UNREGISTERED_TOOLS = [
    get_database_tables,
    get_table_columns,
    get_table_profile,
    get_table_relationships,
    search_metadata,
    search_business_terms,
    search_knowledge_pairs,
]
