"""
Metadata tools: stored column statistics and value search, from the Jeen metadata service.

The connection's catalog itself is not a tool: it is loaded for every run and shown to the
model by CatalogMiddleware (deep_agent.middleware.catalog).
"""

import logging
from typing import Any, Optional

from langchain_core.tools import tool

from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from deep_agent.context import Runtime, connection_id
from deep_agent.tools.envelope import METADATA_ERROR, NOT_FOUND, error, fail, ok

logger = logging.getLogger(__name__)


def metadata_failure(what: str, exc: Exception) -> str:
    logger.warning("%s failed: %s", what, exc)
    return fail(error(
        METADATA_ERROR,
        f"{what} failed: {exc}. The metadata service failed — this does not mean the data is empty.",
    ))


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
        return metadata_failure(f"get_column_profile({table_name}.{column_name})", e)
    if not profile:
        return fail(error(NOT_FOUND, f"No stored profile for column {table_name}.{column_name}"))
    return ok(profile)


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

    Returns data.matches — up to `limit` matching values (use each 'value' verbatim in SQL) with:
    - 'keyword_similarity' close to 1.0 = strong lexical match
    - 'semantic_distance' close to 0.0 = strong semantic match
    data.limit_reached is true when exactly `limit` matches came back: there may be more — call again
    with a higher `limit` before filtering on the list.

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
        return metadata_failure("search_column_values", e)

    values = (res.get("values") or []) if isinstance(res, dict) else []
    matches = [_value_match(v) for v in values]
    limit_reached = len(matches) >= limit
    if not matches:
        notice = "No matches — the value may not exist in this column, or the column is not indexed (only large_categorical / large_unit_id are)."
    elif limit_reached:
        notice = f"Returned the first {limit} matches (the limit); there may be more — call again with a higher limit."
    else:
        notice = None
    return ok({
        "search_term": search_term,
        "matches": matches,
        "limit_reached": limit_reached,
        "notice": notice,
    })
