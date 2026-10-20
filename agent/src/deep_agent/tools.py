"""
Deep Agent tools.

Every tool returns the same JSON envelope (see deep_agent.envelope):
    {"ok": true, "data": ..., "error": null} | {"ok": false, "data": null, "error": {...}}
"""

import logging
import re
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
from agent.utils.sql_guard import clamp
from core.trino import execute_query_sync
from deep_agent import trino_errors
from deep_agent.context import DeepAgentContext, cached_catalog, capability, connection_id, latest_user_request
from deep_agent.envelope import fail, ok

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database Catalog & Schema
# ---------------------------------------------------------------------------

Runtime = ToolRuntime[DeepAgentContext]


def _metadata_failure(what: str, exc: Exception) -> str:
    logger.warning("%s failed: %s", what, exc)
    return fail("METADATA_ERROR", f"{what} failed: {exc}")


@tool
async def get_database_catalog(runtime: Runtime = None) -> str | Command:
    """
    Retrieves the complete metadata catalog and structural overview for the database.

    Use this FIRST when starting any new query to discover what tables exist, how they relate,
    and to see column profiling statistics including semantic types, null ratios, and distinct counts.

    data.catalog contains Column/Table Names, Descriptions, Types, Universal Base Metrics (Null Rate,
    Distinct Count, Cardinality Ratio) and specific Profiling Data Payloads.
    data.capabilities tells you which discovery sources exist for this connection
    (knowledge_pairs, business_terms, relationships) — skip the tools for sources that are false.

    Column semantic_type values to watch for:
    - 'categorical' — small finite value set; values are in the catalog or via get_column_distinct_values.
    - 'large_categorical' / 'large_unit_id' — too many values to enumerate; use search_column_values.
    - 'geo' — WKT geometry column; use resolve_location_to_wkt to build filter values.

    Do NOT use this to fetch live data rows; this only retrieves metadata and schema definitions.
    """
    client = get_jeen_metadata_client()
    try:
        catalog_prompt = await client.get_catalog_prompt(connection_id=connection_id(runtime))
    except Exception as e:
        return _metadata_failure("get_database_catalog", e)

    lower_prompt = catalog_prompt.lower()
    capabilities = {
        "knowledge_pairs": "(no knowledge pairs)" not in lower_prompt,
        "business_terms": "(no business terms)" not in lower_prompt,
        "relationships": "(no relationships)" not in lower_prompt,
    }
    content = ok({"catalog": catalog_prompt, "capabilities": capabilities})
    if runtime is None or not runtime.tool_call_id:
        return content
    # Record capabilities + raw catalog in graph state so later tool calls in this thread can read them.
    return Command(update={
        "connection_capabilities": capabilities,
        "jeen_catalog": catalog_prompt,
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name="get_database_catalog")],
    })


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
        table_name (str): The table identifier (e.g. '"postgres"."public"."flights_table"' or 'flights_table').
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
    if not capability(runtime, "relationships"):
        return ok({
            "relationships": [],
            "notice": "This connection has NO foreign key relationships defined. Infer joins using matching column names, e.g. flight_id.",
        })
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
        return fail("NOT_FOUND", f"No stored profile for table {table_name}")
    return ok(profile)


@tool
async def get_column_profile(table_name: str, column_name: str, runtime: Runtime = None) -> str:
    """
    Retrieves the latest stored statistics for a single specific column.

    Use this before writing a WHERE clause to learn exactly how values are formatted in the database.
    If 'domainIsComplete: true', the listed example values are the ENTIRE value set — any other
    value can be safely ruled out. The 'semanticType' field indicates how to search the column
    (e.g., categorical, large_categorical, geo).

    Do NOT use this for live dynamic querying of values; this reads pre-computed metadata only.

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
        return fail("NOT_FOUND", f"No stored profile for column {table_name}.{column_name}")
    return ok(profile)


# ---------------------------------------------------------------------------
# Query Execution
# ---------------------------------------------------------------------------

def _guard_failure(e: sql_guard.GuardError) -> str:
    error = trino_errors.classify_message("FORBIDDEN_STATEMENT", str(e))
    return fail(**_error_kwargs(error.to_error()))


def _error_kwargs(error: dict) -> dict:
    return {"category": error.pop("category"), "message": error.pop("message"), "hint": error.pop("hint"), **error}


def _run_sql(sql: str, row_cap: int) -> str:
    """Execute already-guarded SQL that fetches up to row_cap + 1 rows; report truncation."""
    try:
        res = execute_query_sync(sql)
    except Exception as e:
        return fail(**_error_kwargs(trino_errors.classify_message(None, str(e)).to_error()))
    if not res.success:
        return fail(**_error_kwargs(trino_errors.classify_result(res).to_error()))

    rows = res.rows or []
    truncated = len(rows) > row_cap
    rows = rows[:row_cap]
    return ok({
        "columns": res.columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows,
        "query_id": res.query_id,
    })


@tool
def execute_trino_query(sql: str, limit: int = 50) -> str:
    """
    Executes a read-only Trino SQL query and returns the resulting rows.

    Use this to test SQL queries against the actual database and perform exploratory data analysis.
    Only a single SELECT / WITH (or SHOW / DESCRIBE) statement is accepted; anything that writes,
    multiple statements, SQL comments and TABLE(...) pass-through functions are rejected with
    error.category FORBIDDEN_STATEMENT.

    Results are capped: an outer LIMIT is enforced automatically, and data.truncated is true
    when more rows existed than were returned.

    On failure, error.category tells you how to fix it precisely:
    - SYNTAX_ERROR: fix the specific syntax — do not redesign the query
    - TABLE_NOT_FOUND / COLUMN_NOT_FOUND: re-verify exact identifiers from the catalog
    - TYPE_MISMATCH: add an explicit CAST(col AS type)
    - FUNCTION_NOT_FOUND: check the Trino function name
    - FUNCTION_SIGNATURE: the function exists but argument types are wrong — CAST the arguments
    - GROUP_BY_ERROR / DIVISION_BY_ZERO / INVALID_VALUE: fix grouping, wrap divisor in NULLIF, use TRY_CAST
    - TIMEOUT_OR_RESOURCE: simplify the query or add a tighter LIMIT
    - INFRASTRUCTURE_ERROR: not a SQL problem — do not rewrite the SQL; retry once
    - FORBIDDEN_STATEMENT: rewrite as a single read-only query without comments
    When available, error.location gives the {line, column} of the offending token.

    Args:
        sql (str): The exact Trino SQL query string to execute. Markdown fences and trailing
                   semicolons are stripped automatically.
        limit (int): The maximum number of rows to return (1–100). Defaults to 50.
    """
    row_cap = clamp(limit, 1, settings.DEEP_AGENT_MAX_ROWS, 50)
    try:
        guarded = sql_guard.prepare(clean_sql(sql), row_cap)
    except sql_guard.GuardError as e:
        return _guard_failure(e)
    return _run_sql(guarded.sql, guarded.row_cap if guarded.is_query else settings.DEEP_AGENT_MAX_ROWS)


@tool
def get_sample_rows(table_name: str, limit: int = 5) -> str:
    """
    Retrieves a small number of raw data rows from a table using a live Trino query.

    Use this only when schema profiles don't provide enough context and you must see exact raw
    data formats (e.g., date formatting, nested JSON structure, exact string casing).
    Do NOT use this for counting or aggregating data.

    Args:
        table_name (str): The fully qualified exact name of the table, e.g. '"catalog"."schema"."table"'.
        limit (int): The number of rows to sample (1–10). Defaults to 5.
    """
    row_cap = clamp(limit, 1, settings.DEEP_AGENT_MAX_SAMPLE_ROWS, 5)
    try:
        sql = sql_guard.sample_rows_sql(table_name, row_cap)
    except sql_guard.GuardError as e:
        return _guard_failure(e)
    return _run_sql(sql, row_cap)


@tool
def get_column_distinct_values(table_name: str, column_name: str, limit: int = 50) -> str:
    """
    Executes a live Trino query to fetch unique values for a specific column.
    Use this to see exact existing strings in the database before writing an exact-match WHERE clause.

    BEST USED FOR:
    - Low-cardinality `categorical` columns (e.g. status, priority, category) where you want to see all
      5-20 possible valid values, and they aren't already complete in the catalog (`domainIsComplete: false`).

    DO NOT USE FOR:
    - High-cardinality columns (`large_categorical`, `large_unit_id`, names, SKUs, cities). Dumping 50-100
      random values from a table with thousands of entities wastes tokens and latency without finding the
      specific entity you need. Use `search_column_values` instead for targeted entity search.
    - Numeric, date, or continuous columns (checking unique values is meaningless for value matching).
    - Columns where the catalog already says `domainIsComplete: true` (all values are already known).

    Args:
        table_name (str): The fully qualified exact name of the table.
        column_name (str): The exact name of the column (plain or double-quoted; spaces and Hebrew are fine).
        limit (int): Maximum distinct values to return (1–100). Defaults to 50.
    """
    row_cap = clamp(limit, 1, settings.DEEP_AGENT_MAX_ROWS, 50)
    try:
        sql = sql_guard.distinct_values_sql(table_name, column_name, row_cap)
    except sql_guard.GuardError as e:
        return _guard_failure(e)
    return _run_sql(sql, row_cap)


# ---------------------------------------------------------------------------
# SQL Quality
# ---------------------------------------------------------------------------

@tool
def validate_sql_syntax(sql: str) -> str:
    """
    Validates a Trino SQL query without executing it against data (syntax AND semantics:
    table/column names, types, function names and signatures are all checked by the planner).

    Use this to dry-run a new draft and catch errors cheaply before execute_trino_query.
    Do NOT use this to check if a query returns 0 rows; it never reads data.

    Returns ok=true with data.valid=true, or ok=false with the same error categories as
    execute_trino_query.

    Args:
        sql (str): The exact Trino SQL query string to validate.
    """
    sql_clean = clean_sql(sql)
    try:
        sql_guard.parse_read_statement(sql_clean)
    except sql_guard.GuardError as e:
        return _guard_failure(e)
    try:
        res = execute_query_sync(f"EXPLAIN (TYPE VALIDATE) {sql_clean}")
    except Exception as e:
        return fail(**_error_kwargs(trino_errors.classify_message(None, str(e)).to_error()))
    if res.success:
        return ok({"valid": True})
    return fail(**_error_kwargs(trino_errors.classify_result(res).to_error()))


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
    if not capability(runtime, "business_terms"):
        return ok({"terms": [], "notice": "This connection has NO business terms defined. Do not call this tool again."})
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
    if not capability(runtime, "knowledge_pairs"):
        return ok({"pairs": [], "notice": "This connection has NO knowledge pairs defined. Do not call this tool again."})
    try:
        pairs = await get_jeen_metadata_client().search_knowledge_pairs(query, limit, connection_id=connection_id(runtime))
        return ok(pairs)
    except Exception as e:
        return _metadata_failure("search_knowledge_pairs", e)


# A value that ends in a number after some non-digit text, e.g. 'Tel Aviv 57' or 'תל אביב 1'.
_COMPOSITE_VALUE = re.compile(r"^(.*\D)\s*\d+$")


def _round_or_none(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _summarise_value_matches(values: list, limit: int) -> tuple[list[dict], dict | None]:
    matches = []
    for v in values[:limit]:
        if not isinstance(v, dict):
            matches.append({"value": v})
            continue
        matches.append({
            "value": v.get("value"),
            "table": v.get("table_name") or v.get("table"),
            "column": v.get("column_name") or v.get("column"),
            "keyword_similarity": _round_or_none(v.get("keyword_similarity")),
            "semantic_distance": _round_or_none(v.get("semantic_distance")),
        })

    # Detect composite prefix pattern among the strongest matches.
    prefixes = []
    for m in matches[:5]:
        hit = _COMPOSITE_VALUE.match(str(m.get("value") or "").strip())
        if hit and hit.group(1).strip():
            prefixes.append(hit.group(1).strip())
    prefix_pattern = None
    if prefixes:
        common = max(set(prefixes), key=prefixes.count)
        if prefixes.count(common) >= 2:
            prefix_pattern = {
                "prefix": common,
                "like": f"{common}%",
                "note": "Values share a prefix (name + code): use LIKE on the prefix to match all variants, or pick one value.",
            }
    return matches, prefix_pattern


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
    ⚠️  Do NOT use on standard `categorical` or `boolean` columns (e.g. `launch_country`) — they are
    not indexed in vector search and will return 0 matches! Use `get_column_distinct_values` or the catalog instead.
    Do NOT use on numeric, date/timestamp, or geometry columns.

    WHEN TO USE:
    - Use whenever you need to find how a high-cardinality entity is represented or spelled in the database
      (e.g., finding 'Tel Aviv 1' from 'תל אביב' or 'Acme Corp' from 'Acme'). Multilingual,
      keyword, and vector search finds the exact database spelling.

    Returns data.matches (use each 'value' verbatim in SQL), with:
    - 'keyword_similarity' close to 1.0 = strong lexical match
    - 'semantic_distance' close to 0.0 = strong semantic match
    and data.prefix_pattern when several matches share a prefix (e.g. 'Tel Aviv 1', 'Tel Aviv 57'):
    the column stores composite strings — use LIKE 'prefix%' to match all variants.

    Args:
        search_term (str): The specific value to look for (partial or full string, Hebrew or English).
        table (str, optional): Restrict search to one table name. Omit to search all tables.
        column (str, optional): Restrict search to one column name. Omit to search all columns.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    limit = clamp(limit, 1, 50, 10)
    try:
        res = await get_jeen_metadata_client().search_column_values(
            search_term=search_term, table=table, column=column, limit=limit, raw=True,
            connection_id=connection_id(runtime),
        )
    except Exception as e:
        return _metadata_failure("search_column_values", e)

    values = res.get("values") or [] if isinstance(res, dict) else []
    matches, prefix_pattern = _summarise_value_matches(values, limit)
    return ok({
        "search_term": search_term,
        "matches": matches,
        "prefix_pattern": prefix_pattern,
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
            return fail("ENRICHMENT_ERROR", "No tables could be parsed from the connection catalog.",
                        original_sql=sql_clean)

        refined_sql, plan, enriched = await EnrichmentOrchestrator.enrich_query(
            user_request=latest_user_request(runtime),
            initial_sql=sql_clean,
            schema=schema,
            tables=tables,
            config=runtime.config if runtime is not None else None,
        )
    except Exception as e:
        logger.error("enrich_sql_filters failed: %s", e, exc_info=True)
        return fail("ENRICHMENT_ERROR", str(e), original_sql=sql_clean)

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
    Returns data.sql_literal — a ready-to-embed, single-quoted SQL string literal:
    ST_GeometryFromText(<data.sql_literal>) — plus data.wkt (the same geometry, unquoted).

    The function uses rate-limited Nominatim geocoding with caching and automatic WKT
    simplification (binary search). Only area geometries are returned (never points).

    Do NOT use this for exact addresses (e.g., "Rothschild 10, Tel Aviv"); it is designed for
    administrative areas: cities, regions, districts, and countries.

    Args:
        location_name (str): The geographic location name to resolve. Hebrew names are
                             accepted and preferred for Israeli locations.
    """
    geojson = get_geojson_polygon(location_name)
    sql_literal = geojson_to_simplified_wkt(geojson) if geojson else None  # already single-quoted
    if not sql_literal:
        return fail("LOCATION_NOT_FOUND", f"Could not resolve location '{location_name}' to an area polygon.")
    return ok({"location": location_name, "sql_literal": sql_literal, "wkt": sql_literal.strip("'")})


# The agent's full domain tool set (order = order shown to the model).
DEEP_AGENT_TOOLS = [
    # Discovery & Schema
    get_database_catalog,
    get_database_tables,
    get_table_columns,
    get_table_relationships,
    get_table_profile,
    get_column_profile,
    # Search & Resolve
    search_metadata,
    search_business_terms,
    search_knowledge_pairs,
    search_column_values,
    resolve_location_to_wkt,
    # Query Execution
    execute_trino_query,
    get_sample_rows,
    get_column_distinct_values,
    validate_sql_syntax,
    # Post-processing
    enrich_sql_filters,
]
