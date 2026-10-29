"""
enrich_sql_filters: correct categorical filter values in a draft SQL to the stored spellings,
reusing the legacy graph's enrichment pipeline (agent.services.enrichment_orchestrator).
"""

import logging

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from agent.utils.sql import clean_sql
from deep_agent.context import Runtime, cached_catalog, connection_id, user_request
from deep_agent.tools.envelope import ENRICHMENT_ERROR, error, fail, ok
from deep_agent.tools.metadata import metadata_failure

logger = logging.getLogger(__name__)


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
            return metadata_failure("enrich_sql_filters (catalog fetch)", e)
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
