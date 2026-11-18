import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from langchain_openai import ChatOpenAI

from agent.config import settings

logger = logging.getLogger(__name__)

# The model the caller chose for this request (UI dropdown -> backend -> MCP tool). It overrides every node's model.
_selected_model: ContextVar[str | None] = ContextVar("selected_model", default=None)
_models_cache: tuple[float, list[str]] | None = None
MODELS_CACHE_TTL_S = 60.0


class UnknownModelError(ValueError):
    """The requested model is not served by LLM_BASE_URL."""


def available_models() -> list[str]:
    """The model ids LLM_BASE_URL serves (OpenAI-compatible GET /models with LLM_API_KEY), cached briefly."""
    global _models_cache
    if _models_cache is not None and _models_cache[0] > time.monotonic():
        return list(_models_cache[1])
    from openai import OpenAI

    client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, timeout=15.0)
    models = sorted(m.id for m in client.models.list())
    _models_cache = (time.monotonic() + MODELS_CACHE_TTL_S, models)
    return list(models)


def check_model(model: str) -> str:
    """`model` if LLM_BASE_URL serves it, else UnknownModelError (with the models it does serve)."""
    served = available_models()
    if model not in served:
        raise UnknownModelError(f"Model '{model}' is not available. Available: {', '.join(served) or 'none'}")
    return model


@contextmanager
def selected_model(model: str | None) -> Iterator[None]:
    """Within this block every get_llm() call uses `model` (None = the configured defaults)."""
    token = _selected_model.set(model)
    try:
        yield
    finally:
        _selected_model.reset(token)

# Flag name → LLM_MODEL env-var fallback for each node
_NODE_MODEL_FLAGS: dict[str, str] = {
    "extractor":          "EXTRACTOR_MODEL",
    "location_extractor": "LOCATION_EXTRACTOR_MODEL",
    "schema_explorer":    "SCHEMA_EXPLORER_MODEL",
    "query_builder":      "QUERY_BUILDER_MODEL",
    "refiner":            "REFINER_MODEL",
    "satisfaction_check": "SATISFACTION_JUDGE_MODEL",
    "routing":            "QUERY_BUILDER_MODEL",  # rejection router reuses QB model
    "default":            "QUERY_BUILDER_MODEL",
}

_NODE_TEMP_FLAGS: dict[str, str] = {
    "extractor":          "EXTRACTOR_TEMPERATURE",
    "location_extractor": "LOCATION_EXTRACTOR_TEMPERATURE",
    "query_builder":      "QUERY_BUILDER_TEMPERATURE",
}


def get_llm(
    node: str = "default",
    temperature: Optional[float] = None,
    runtime_flags: Optional[dict] = None,
    model: Optional[str] = None,
) -> ChatOpenAI:
    """
    Factory for per-node LLM instances.

    Priority for model selection: the `model` argument, then the request's selected model (selected_model()),
    then runtime_flags (resolved by init_flags_node from DB + execution mode), then AgentSettings defaults.
    Temperature: runtime_flags, then AgentSettings defaults.

    Args:
        node:          Name of the calling graph node (used to pick the right flag).
        temperature:   Optional hard override — bypasses flag resolution.
        runtime_flags: The state["runtime_flags"] dict from the current invocation.
                       Pass None when initialising at module level (will use env defaults).
    """
    flags = runtime_flags or {}

    # Resolve model
    model_flag = _NODE_MODEL_FLAGS.get(node, "QUERY_BUILDER_MODEL")
    model = model or _selected_model.get() or flags.get(model_flag) or settings.LLM_MODEL

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

