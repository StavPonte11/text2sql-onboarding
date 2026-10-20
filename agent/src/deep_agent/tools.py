import json
import logging
import asyncio
from typing import Optional
from langchain_core.tools import tool

from agent.config import settings
from agent.utils import sql_guard
from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from agent.utils.sql import clean_sql
from agent.utils.sql_guard import clamp
from core.trino import execute_query_sync
from agent.services.geo_utils import get_geojson_polygon, geojson_to_simplified_wkt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database Catalog & Schema
# ---------------------------------------------------------------------------

# Connection capability flags dynamically inferred from the catalog prompt
_connection_capabilities: dict[str, bool] = {
    "has_knowledge_pairs": True,
    "has_business_terms": True,
    "has_relationships": True,
}


@tool
async def get_database_catalog() -> str:
    """
    Retrieves the complete metadata catalog and structural overview for the database.

    Use this FIRST when starting any new query to discover what tables exist, how they relate,
    and to see column profiling statistics including semantic types, null ratios, and distinct counts.

    The output includes Column/Table Names, Descriptions, Types, Universal Base Metrics (Null Rate,
    Distinct Count, Cardinality Ratio) and specific Profiling Data Payloads.

    Column semantic_type values to watch for:
    - 'categorical' — small finite value set; use search_column_values to resolve exact spellings.
    - 'large_categorical' — too many values to enumerate; use search_column_values with query terms.
    - 'geo' — WKT geometry column; use resolve_location_to_wkt to build filter values.

    Do NOT use this to fetch live data rows; this only retrieves metadata and schema definitions.
    """
    client = get_jeen_metadata_client()
    try:
        catalog_prompt = await client.get_catalog_prompt()

        lower_prompt = catalog_prompt.lower()
        has_kp = "(no knowledge pairs)" not in lower_prompt
        has_bt = "(no business terms)" not in lower_prompt
        has_fk = "(no relationships)" not in lower_prompt

        _connection_capabilities["has_knowledge_pairs"] = has_kp
        _connection_capabilities["has_business_terms"] = has_bt
        _connection_capabilities["has_relationships"] = has_fk

        footer_lines = [
            "\n\n---",
            "### Discovery Capabilities Note:",
            f"- Knowledge Pairs: {'Available' if has_kp else 'None (skip search_knowledge_pairs)'}",
            f"- Business Terms: {'Available' if has_bt else 'None (skip search_business_terms)'}",
            f"- Foreign Keys: {'Available' if has_fk else 'None (infer joins from column names)'}",
        ]
        return catalog_prompt + "\n".join(footer_lines)
    except Exception as e:
        return f"Error fetching catalog: {str(e)}"


# @tool
# async def get_filtered_prompt(question: str) -> str:
#     """
#     Retrieves a narrowed, highly focused catalog prompt scoped specifically to the user's question.
#     Contains the AI-selected most relevant columns, primary keys, foreign key joins, and relevant
#     knowledge pairs / business terms.

#     Use this when you want a compact, fast overview of just the tables and columns relevant to
#     the current question without reading the massive full database catalog.

#     Args:
#         question (str): The natural language question (Hebrew or English).
#     """
#     client = get_jeen_metadata_client()
#     try:
#         res = await client.get_filtered_prompt(question)
#         if isinstance(res, dict) and "prompt" in res:
#             return res["prompt"]
#         return json.dumps(res, indent=2, ensure_ascii=False)
#     except Exception as e:
#         return f"Error fetching filtered prompt: {e}"


# @tool
# async def resolve_question(question: str) -> str:
#     """
#     Explains how a question maps onto this connection's curated vocabulary:
#     which glossary terms, synonyms, acronyms, tables and columns it mentions and what they resolve to.

#     Use this when encountering ambiguous domain abbreviations, jargon, or Hebrew concepts to
#     see the curated glossary and lexicon mappings.

#     Args:
#         question (str): The natural language question or phrase in Hebrew or English.
#     """
#     client = get_jeen_metadata_client()
#     try:
#         res = await client.resolve_question(question)
#         return json.dumps(res, indent=2, ensure_ascii=False)
#     except Exception as e:
#         return f"Error resolving question: {e}"


@tool
async def get_database_tables() -> str:
    """
    Returns the list of all tables in the connection with their business descriptions and
    exact quoted identifiers ("catalog"."schema"."table") for querying.
    Use this for a quick, clean roster of available tables.
    """
    client = get_jeen_metadata_client()
    try:
        tables = await client.get_tables()
        return json.dumps(tables, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching tables: {e}"


@tool
async def get_table_columns(table_name: str) -> str:
    """
    Returns the columns for a specific table: data type, primary key flag, business description,
    real example values, and profiling statistics.

    Args:
        table_name (str): The table identifier (e.g. '"postgres"."public"."flights_table"' or 'flights_table').
    """
    client = get_jeen_metadata_client()
    try:
        cols = await client.get_columns(table_name)
        return json.dumps(cols, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching columns for {table_name}: {e}"


@tool
async def get_table_relationships() -> str:
    """
    Returns the source-scoped foreign-key and semantic join relationships between tables.
    Use this to identify exact join paths and foreign key pairs.
    """
    if not _connection_capabilities.get("has_relationships", True):
        return "[] (Notice: This connection has NO foreign key relationships defined in the catalog. Infer joins using matching column names, e.g. flight_id.)"
    client = get_jeen_metadata_client()
    try:
        rels = await client.get_relationships()
        return json.dumps(rels, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching relationships: {e}"


@tool
async def get_table_profile(table_name: str) -> str:
    """
    Retrieves the latest stored schema and statistics for one specific table.

    Use this to understand a single table's row counts, column roles, null ratios, distinct counts,
    and min/max values before writing SQL against it.
    Do NOT use this to fetch raw data rows; use `get_sample_rows` or `execute_trino_query` instead.

    Args:
        table_name (str): The fully qualified, exact 3-part name of the table,
                          e.g., '"catalog"."schema"."table"'.
    """
    client = get_jeen_metadata_client()
    try:
        profile = await client.get_table_profile(table_name)
        if not profile:
            return f"Error: Schema not found for table {table_name}"
        return json.dumps(profile, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching table schema: {str(e)}"


@tool
async def get_column_profile(table_name: str, column_name: str) -> str:
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
    client = get_jeen_metadata_client()
    try:
        profile = await client.get_column_profile(table_name, column_name)
        if not profile:
            return f"Error: Column profile not found for {table_name}.{column_name}"
        return json.dumps(profile, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching column profile: {str(e)}"


# ---------------------------------------------------------------------------
# Query Execution
# ---------------------------------------------------------------------------

def _classify_trino_error(error_message: str) -> str:
    """Classify a Trino error message into an actionable category."""
    msg = error_message.lower()
    if any(k in msg for k in ["syntax error", "mismatched input", "parse error", "unexpected"]):
        return "SYNTAX_ERROR"
    if any(k in msg for k in ["table not found", "schema not found", "catalog not found", "does not exist"]):
        return "TABLE_NOT_FOUND"
    if any(k in msg for k in ["column", "field", "not found in", "cannot resolve"]):
        return "COLUMN_NOT_FOUND"
    if any(k in msg for k in ["type mismatch", "cannot cast", "invalid cast", "expected", "varchar", "bigint", "double"]):
        return "TYPE_MISMATCH"
    if any(k in msg for k in ["function not found", "no function", "not a function"]):
        return "FUNCTION_NOT_FOUND"
    if any(k in msg for k in ["timeout", "exceeded", "resource", "query killed"]):
        return "TIMEOUT_OR_RESOURCE"
    return "EXECUTION_ERROR"


_HINTS = {
    "SYNTAX_ERROR": "Fix the specific syntax line — do not redesign the entire query.",
    "TABLE_NOT_FOUND": "Re-verify the exact 3-part table name from get_database_catalog.",
    "COLUMN_NOT_FOUND": "Re-verify the column name from get_table_columns for this table.",
    "TYPE_MISMATCH": "Add CAST(column AS target_type) for the mismatched column.",
    "FUNCTION_NOT_FOUND": "Check Trino function spelling. E.g., use ST_GeometryFromText, not ST_GeomFromText.",
    "TIMEOUT_OR_RESOURCE": "Add a tighter LIMIT or simplify the query.",
    "FORBIDDEN_STATEMENT": "Only a single read-only SELECT/WITH (or SHOW/DESCRIBE) statement without comments is allowed.",
    "EXECUTION_ERROR": "Investigate the specific error message.",
}


def _error_result(error_category: str, error_message: str) -> str:
    return json.dumps({
        "success": False,
        "error_category": error_category,
        "error_message": error_message,
        "hint": _HINTS.get(error_category, "Investigate the error message."),
    }, ensure_ascii=False)


def _run_sql(sql: str, row_cap: int) -> str:
    """Execute already-guarded SQL that fetches up to row_cap + 1 rows; report truncation."""
    try:
        res = execute_query_sync(sql)
    except Exception as e:
        return _error_result("EXECUTION_ERROR", str(e))
    if not res.success:
        error_msg = res.error_message or "Unknown error"
        return _error_result(_classify_trino_error(error_msg), error_msg)

    rows = res.rows or []
    truncated = len(rows) > row_cap
    rows = rows[:row_cap]
    return json.dumps({
        "success": True,
        "columns": res.columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows,
    }, ensure_ascii=False, default=str)


@tool
def execute_trino_query(sql: str, limit: int = 50) -> str:
    """
    Executes a read-only Trino SQL query and returns the resulting rows.

    Use this to test SQL queries against the actual database and perform exploratory data analysis.
    Only a single SELECT / WITH (or SHOW / DESCRIBE) statement is accepted; anything that writes,
    multiple statements, SQL comments and TABLE(...) pass-through functions are rejected with
    error_category FORBIDDEN_STATEMENT.

    Results are capped: an outer LIMIT is enforced automatically, and the response includes
    'truncated': true when more rows existed than were returned.

    On failure, the response includes an 'error_category' field to help you classify and fix
    the error precisely:
    - SYNTAX_ERROR: fix the specific syntax — do not redesign the query
    - TABLE_NOT_FOUND / COLUMN_NOT_FOUND: re-verify exact identifiers from the catalog
    - TYPE_MISMATCH: add an explicit CAST(col AS type)
    - FUNCTION_NOT_FOUND: check the Trino function name
    - TIMEOUT_OR_RESOURCE: simplify the query or add a tighter LIMIT
    - FORBIDDEN_STATEMENT: rewrite as a single read-only query without comments

    Args:
        sql (str): The exact Trino SQL query string to execute. Markdown fences and trailing
                   semicolons are stripped automatically.
        limit (int): The maximum number of rows to return (1–100). Defaults to 50.
    """
    row_cap = clamp(limit, 1, settings.DEEP_AGENT_MAX_ROWS, 50)
    try:
        guarded = sql_guard.prepare(clean_sql(sql), row_cap)
    except sql_guard.GuardError as e:
        return _error_result("FORBIDDEN_STATEMENT", str(e))
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
        return _error_result("FORBIDDEN_STATEMENT", str(e))
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
        return _error_result("FORBIDDEN_STATEMENT", str(e))
    return _run_sql(sql, row_cap)


# ---------------------------------------------------------------------------
# SQL Quality
# ---------------------------------------------------------------------------

@tool
def validate_sql_syntax(sql: str) -> str:
    """
    Validates the syntax of a Trino SQL query without executing it against data.

    Use this to dry-run a query and instantly catch syntax errors (missing CASTs, typos, bad
    function names). This is cheap — always call this before execute_trino_query on a new draft.
    Do NOT use this to check if a query returns 0 rows; it only checks syntax validity.

    Args:
        sql (str): The exact Trino SQL query string to validate.
    """
    sql_clean = clean_sql(sql)
    try:
        sql_guard.parse_read_statement(sql_clean)
    except sql_guard.GuardError as e:
        return f"Syntax Error: {e}"
    try:
        res = execute_query_sync(f"EXPLAIN (TYPE VALIDATE) {sql_clean}")
        if res.success:
            return "Syntax is VALID."
        else:
            return f"Syntax Error: {res.error_message}"
    except Exception as e:
        return f"Validation failed: {str(e)}"


# ---------------------------------------------------------------------------
# Metadata Search
# ---------------------------------------------------------------------------

@tool
async def search_metadata(query: str, limit: int = 10) -> str:
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
    client = get_jeen_metadata_client()
    res = await client.search(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)


@tool
async def search_business_terms(query: str, limit: int = 10) -> str:
    """
    Searches the business glossary for terms relevant to a natural language question.

    Use this to bridge the gap between user vocabulary and database terminology
    (e.g., matching 'מכירות' to the 'Revenue' column). Ranked by semantic meaning, not keywords.
    Do NOT use this to search for database tables or column names directly.

    Args:
        query (str): A natural language question or phrase (multilingual support).
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    if not _connection_capabilities.get("has_business_terms", True):
        return "[] (Notice: This connection has NO business terms defined in the catalog. Do not call this tool again.)"
    client = get_jeen_metadata_client()
    res = await client.search_business_terms(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)


@tool
async def search_knowledge_pairs(query: str, limit: int = 10) -> str:
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
    if not _connection_capabilities.get("has_knowledge_pairs", True):
        return "[] (Notice: This connection has NO knowledge pairs defined in the catalog. Do not call this tool again.)"
    client = get_jeen_metadata_client()
    res = await client.search_knowledge_pairs(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)


@tool
async def search_column_values(
    search_term: str,
    table: Optional[str] = None,
    column: Optional[str] = None,
    limit: int = 10,
) -> str:
    """
    Searches for exact real values contained within indexed columns across the database.

    SUPPORTED COLUMN TYPES:
    Works exclusively on high-cardinality columns profiled as:
    # - `boolean` (not indexed in search MCP — use catalog or get_column_distinct_values)
    # - `categorical` (not indexed in search MCP — use catalog or get_column_distinct_values)
    - `large_categorical`
    - `large_unit_id`
    ⚠️  Do NOT use on standard `categorical` or `boolean` columns (e.g. `launch_country`) — they are
    not indexed in vector search and will return 0 matches! Use `get_column_distinct_values` or the catalog instead.
    Do NOT use on numeric, date/timestamp, or geometry columns.

    WHEN TO USE:
    - Use whenever you need to find how a high-cardinality entity is represented or spelled in the database
      (e.g., finding 'Tel Aviv 1' from 'תל אביב' or 'Acme Corp' from 'Acme'). Multilingual,
      keyword, and vector search finds the exact database spelling.
    - Essential for high-cardinality columns (`large_categorical`, `large_unit_id`) where dumping
      all distinct values is infeasible.

    The response includes:
    - A 'top_matches' summary listing the exact values you should use in SQL
    - If multiple values share a common prefix (e.g., 'Tel Aviv 1', 'Tel Aviv 57'),
      the column stores composite strings — use LIKE 'prefix%' in SQL to match all variants
    - 'keyword_similarity' close to 1.0 = strong lexical match
    - 'semantic_distance' close to 0.0 = strong semantic match

    Args:
        search_term (str): The specific value to look for (partial or full string, Hebrew or English).
        table (str, optional): Restrict search to one table name. Omit to search all tables.
        column (str, optional): Restrict search to one column name. Omit to search all columns.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    client = get_jeen_metadata_client()
    res = await client.search_column_values(search_term=search_term, table=table, column=column, limit=limit, raw=True)

    # Build an actionable summary of the top matches at the top of the response
    summary_lines = []
    if isinstance(res, dict) and "values" in res:
        values = res["values"]
        if values:
            summary_lines.append(f"TOP MATCHES for '{search_term}' (use these exact strings in SQL):")
            seen_values = []
            for v in values[:5]:
                if isinstance(v, dict):
                    val = v.get("value", "")
                    tbl = v.get("table_name", "")
                    col = v.get("column_name", "")
                    ks = round(v.get("keyword_similarity", 0), 3)
                    sd = round(v.get("semantic_distance", 1), 3)
                    summary_lines.append(f"  - '{val}'  (table: {tbl}, column: {col}, keyword_sim: {ks}, sem_dist: {sd})")
                    seen_values.append(val)

            # Detect composite prefix pattern (e.g., 'Tel Aviv 1', 'Tel Aviv 57')
            import re as _re
            prefixes = []
            for val in seen_values:
                m = _re.match(r'^([A-Za-z\s\'\-\.]+?)\s*\d+$', val.strip())
                if m:
                    prefixes.append(m.group(1).strip())
            if prefixes:
                common_prefix = max(set(prefixes), key=prefixes.count)
                if prefixes.count(common_prefix) >= 2:
                    summary_lines.append(
                        f"\nNOTE: Multiple values share prefix '{common_prefix}' "
                        f"— this column stores composite strings (name + code). "
                        f"Use LIKE '{common_prefix}%' to match all variants, or pick one specific value."
                    )
        else:
            summary_lines.append(f"No matches found for '{search_term}'. The value may not exist in this column.")

    summary = "\n".join(summary_lines)
    full_payload = json.dumps(res, indent=2, ensure_ascii=False)
    return f"{summary}\n\nFull payload:\n{full_payload}"


# ---------------------------------------------------------------------------
# Category Enrichment (Automatic Filter Value Correction)
# ---------------------------------------------------------------------------

@tool
async def enrich_sql_filters(
    user_request: str,
    sql: str,
    jeen_catalog: str,
) -> str:
    """
    Automatically corrects categorical filter values in a draft SQL query to match exact
    database spellings using semantic search and LLM-guided AST transformation.

    Use this AFTER you have written a draft SQL query that contains WHERE clause filters on
    categorical (text) columns. It will:
    1. Parse the SQL AST to find comparison filters
    2. Semantically search the database for canonical matching values
    3. Use an LLM to select the best match
    4. Rewrite the SQL with exact canonical values (AST-level, not string replacement)

    This is especially powerful for Hebrew→English column value resolution and large categorical
    columns where manually calling search_column_values for every filter would be tedious.

    Do NOT use this on purely numeric SQL or when you already have exact confirmed values.

    Returns the enriched SQL string, or the original SQL if no enrichment was needed/possible.

    Args:
        user_request (str): The original natural language request from the user (Hebrew).
        sql (str): The draft SQL query to enrich.
        jeen_catalog (str): The full catalog string (output of get_database_catalog).
    """
    from agent.services.enrichment_orchestrator import EnrichmentOrchestrator
    from agent.nodes.refiner import parse_jeen_catalog_tables

    sql_clean = clean_sql(sql)
    try:
        schema, tables = parse_jeen_catalog_tables(jeen_catalog)
        if not tables:
            return f"Could not parse schema from catalog. Original SQL returned unchanged:\n{sql_clean}"

        refined_sql, plan, enriched = await EnrichmentOrchestrator.enrich_query(
            user_request=user_request,
            initial_sql=sql_clean,
            schema=schema,
            tables=tables,
            config=None,
        )

        if enriched and plan:
            changes = []
            for tf in plan.enrichment_details:
                if tf.changed_filter:
                    changes.append(
                        f"  • column '{tf.column}': '{tf.original_value}' → {tf.refined_values} "
                        f"(operator: {tf.old_operator} → {tf.new_operator})"
                    )
            summary = "\n".join(changes) if changes else "  (no changes logged)"
            return (
                f"✅ SQL enriched successfully. Changes made:\n{summary}\n\n"
                f"Enriched SQL:\n```sql\n{refined_sql}\n```"
            )
        else:
            return (
                f"ℹ️ No categorical filter corrections needed. Original SQL:\n```sql\n{sql_clean}\n```"
            )

    except Exception as e:
        logger.error(f"enrich_sql_filters failed: {e}", exc_info=True)
        return f"Error during enrichment (original SQL unchanged): {e}\n\n```sql\n{sql_clean}\n```"


# ---------------------------------------------------------------------------
# Geospatial
# ---------------------------------------------------------------------------

@tool
def resolve_location_to_wkt(location_name: str) -> str:
    """
    Resolves a geographic location name to its WKT (Well-Known Text) polygon representation.

    Use this whenever the user's query involves a geographic area and you need a WKT polygon for
    a Trino ST_GeometryFromText() / ST_Within() / ST_Intersects() call.

    Supports Hebrew location names directly (e.g., 'תל אביב', 'ירושלים', 'חיפה').
    Returns a simplified polygon quoted WKT string, e.g., 'POLYGON((...))', ready to embed
    directly into SQL: ST_GeometryFromText(<returned_value>).

    The function uses rate-limited Nominatim geocoding with caching and automatic WKT
    simplification (binary search), falling back to a bounding-box polygon if needed.

    Do NOT use this for exact addresses (e.g., "Rothschild 10, Tel Aviv"); it is designed for
    administrative areas: cities, regions, districts, and countries.

    Args:
        location_name (str): The geographic location name to resolve. Hebrew names are
                             accepted and preferred for Israeli locations.
    """
    geojson = get_geojson_polygon(location_name)
    if geojson:
        wkt = geojson_to_simplified_wkt(geojson)
        if wkt:
            return wkt
    return f"Error: Could not resolve location '{location_name}' to WKT."
