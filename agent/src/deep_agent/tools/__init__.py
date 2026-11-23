"""
The Deep Agent's tools.

Every tool returns the same JSON envelope (deep_agent.tools.envelope):
    {"ok": true, "data": ..., "error": null} | {"ok": false, "data": null, "error": {"name", "message", ...}}

- sql.py         execute_trino_query, get_sample_rows, get_column_distinct_values, validate_sql
- metadata.py    search_column_values
- location.py    resolve_location_to_wkt

The connection's catalog is not a tool: CatalogMiddleware (deep_agent.middleware.catalog) loads it
for every run and puts it in the prompt.
"""

from deep_agent.tools.location import resolve_location_to_wkt
from deep_agent.tools.metadata import search_column_values
from deep_agent.tools.sql import execute_trino_query, get_column_distinct_values, get_sample_rows, validate_sql

# The tools the model gets (order = order shown to the model).
DEEP_AGENT_TOOLS = [
    # Values & places
    search_column_values,
    get_column_distinct_values,
    resolve_location_to_wkt,
    # Query execution
    execute_trino_query,
    get_sample_rows,
    validate_sql,
]
