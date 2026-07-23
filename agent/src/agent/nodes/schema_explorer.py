from __future__ import annotations
import asyncio
import json
import logging
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from agent.state import AgentState
from langchain_core.runnables import RunnableConfig

from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.utils.redis_publisher import publish_node_event
from agent.utils.jeen_metadata_client import get_jeen_metadata_client

logger = logging.getLogger(__name__)

# G2-02 limits
MAX_SCHEMA_RETRIES = 3

async def schema_explorer_node(state: AgentState, config: RunnableConfig = None):
    """Schema Explorer node — just fetches the full catalog prompt from MCP."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""

    await publish_node_event(thread_id, "schema_explorer")

    _jeen = get_jeen_metadata_client()
    if not _jeen.is_configured:
        logger.warning("MCP client is not configured, but fallback is disabled. Query builder might fail.")
        catalog_prompt = "No catalog available (MCP not configured)."
    else:
        logger.info("Fetching full catalog prompt from Jeen MCP.")
        catalog_prompt = await _jeen.get_catalog_prompt()

    # ── Langfuse trace metadata ───────────────────────────────────────────────
    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client.update_current_span(
                metadata={
                    "schema_explorer_mode": "mcp_catalog_only",
                },
            )
    except Exception as exc:
        logger.warning("Langfuse trace update failed in schema_explorer: %s", exc)

    result_state: dict = {
        "jeen_catalog": catalog_prompt,
        "tables_used": [], 
        "table_profiles": None, 
        "execution_path": ["schema_explorer"],
        "schema_explorer_retry_count": 0,
        "hallucinated_tables": None,
        "last_error": None
    }

    return result_state
