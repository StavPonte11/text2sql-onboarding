"""
init_flags_node (G4)
====================
Runs immediately after validate_config, before init_skills.

Responsibilities:
  1. Call FlagBridge.resolve_flags(execution_mode) to merge:
       mode overrides → DB flags → env-var defaults
  2. Write the resolved dict to state["runtime_flags"]
  3. Log runtime_flags to Langfuse trace metadata for full observability
"""

import logging

from agent.langfuse_client import langfuse_client
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from agent.utils.flag_bridge import FlagBridge

logger = logging.getLogger(__name__)

_flag_bridge = FlagBridge()


async def init_flags_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """
    Resolve all runtime configuration flags for this invocation.

    The resolved dict is stored in state["runtime_flags"] and read by every
    downstream node instead of directly accessing AgentSettings env vars.
    This guarantees that:
      - DS team changes in the Studio UI take effect within the cache TTL (30s).
      - Execution mode overrides are applied consistently to all nodes.
       Execution mode overrides take precedence over dynamic flags, which override
    the agent's default `settings`.
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    import asyncio
    asyncio.create_task(publish_node_event(thread_id, "init_flags"))

    mode_name = state.get("execution_mode")
    execution_mode: str | None = state.get("execution_mode")

    try:
        runtime_flags = await _flag_bridge.resolve_flags(execution_mode)
    except Exception as exc:
        logger.warning("init_flags_node: FlagBridge failed (%s), using env defaults", exc)
        # FlagBridge already handles its own fallback internally, so this is a
        # safety net for any unexpected error in the bridge itself.
        from agent.utils.flag_bridge import _ENV_DEFAULTS
        runtime_flags = dict(_ENV_DEFAULTS)

    # Emit to Langfuse for observability
    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client.trace(
                id=trace_id,
                metadata={
                    "runtime_flags": runtime_flags,
                    "execution_mode": execution_mode or "default",
                },
            )
    except Exception as exc:
        logger.warning("init_flags_node: Langfuse trace failed: %s", exc)

    logger.info(
        "init_flags_node: resolved %d flags (mode=%s)",
        len(runtime_flags),
        execution_mode or "default",
    )

    return {"runtime_flags": runtime_flags}
