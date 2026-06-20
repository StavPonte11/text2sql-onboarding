import logging
from typing import Optional

from langchain_openai import ChatOpenAI

from agent.config import settings

logger = logging.getLogger(__name__)

# Flag name → LLM_MODEL env-var fallback for each node
_NODE_MODEL_FLAGS: dict[str, str] = {
    "extractor":          "EXTRACTOR_MODEL",
    "schema_explorer":    "SCHEMA_SUMMARY_MODEL",
    "query_builder":      "QUERY_BUILDER_MODEL",
    "refiner":            "REFINER_MODEL",
    "satisfaction_check": "SATISFACTION_JUDGE_MODEL",
    "routing":            "QUERY_BUILDER_MODEL",  # rejection router reuses QB model
    "default":            "QUERY_BUILDER_MODEL",
}

_NODE_TEMP_FLAGS: dict[str, str] = {
    "extractor":     "EXTRACTOR_TEMPERATURE",
    "query_builder": "QUERY_BUILDER_TEMPERATURE",
}


def get_llm(
    node: str = "default",
    temperature: Optional[float] = None,
    runtime_flags: Optional[dict] = None,
) -> ChatOpenAI:
    """
    Factory for per-node LLM instances.

    Priority for model/temperature selection:
      1. runtime_flags (resolved by init_flags_node from DB + execution mode)
      2. AgentSettings env-var defaults

    Args:
        node:          Name of the calling graph node (used to pick the right flag).
        temperature:   Optional hard override — bypasses flag resolution.
        runtime_flags: The state["runtime_flags"] dict from the current invocation.
                       Pass None when initialising at module level (will use env defaults).
    """
    flags = runtime_flags or {}

    # Resolve model
    model_flag = _NODE_MODEL_FLAGS.get(node, "QUERY_BUILDER_MODEL")
    model = flags.get(model_flag) or settings.LLM_MODEL

    # Resolve temperature
    if temperature is None:
        temp_flag = _NODE_TEMP_FLAGS.get(node)
        temperature = float(flags.get(temp_flag, 0.0)) if temp_flag else 0.0

    logger.debug(
        "Instantiating LLM for node='%s': model='%s' temperature=%.2f",
        node,
        model,
        temperature,
    )

    return ChatOpenAI(
        model=model,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        temperature=temperature,
        timeout=300.0,
    )
