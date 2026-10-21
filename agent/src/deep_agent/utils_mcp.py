"""
Curated Utils-Server tools, loaded through the Jarvis Registry MCP gateway.

Deterministic utilities beat prompt rules where they apply (Hebrew text normalisation,
relative-date anchors, spatial-expression validation, buffers around points). They are
optional: with JARVIS_GATEWAY_URL unset, or the gateway unreachable, the agent starts
without them and logs why.

- Endpoint: {JARVIS_GATEWAY_URL}/mcp/{JARVIS_MCP_NAMESPACE}/ (streamable HTTP),
  auth `Authorization: ApiKey <JARVIS_API_KEY>` (the gateway's API-key scheme).
- Only allow-listed tools are used, and each goes only to the agent that needs it
  (UTILS_TOOL_ALLOCATION). SQL never runs through Utils-Server: all execution stays on the
  guarded execute_trino_query path.
- Every tool is wrapped in the Deep Agent envelope ({ok, data, error}); gateway errors
  ({"error", "status_code", "response_text"}) become ok=false with EXTERNAL_TOOL_ERROR.
"""

import asyncio
import json
import logging
import re
import time
from datetime import timedelta
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from agent.config import settings
from deep_agent.envelope import fail, ok

logger = logging.getLogger(__name__)

# Which agent gets which Utils-Server tool ("main" = the top-level Deep Agent).
UTILS_TOOL_ALLOCATION: dict[str, tuple[str, ...]] = {
    "main": ("datetime_parse_relative_expression", "text_normalize", "trino_validate_spatial_expression"),
    "value-resolver": ("datetime_parse_relative_expression", "text_normalize", "geo_create_buffer",
                       "geo_validate_geometry"),
    "schema-scout": (),
    "result-verifier": (),
    # get_geocode_address feeds resolve_location; the rest are offered only when the gateway lists them.
    "location-analyst": ("get_geocode_address", "get_location_by_coordinates", "nominatim_helper",
                         "getcapabilities_core", "getfeatureschema_core", "searchfeatures_core",
                         "trino_validate_spatial_expression"),
}
ALLOWED_UTILS_TOOLS = frozenset(name for names in UTILS_TOOL_ALLOCATION.values() for name in names)


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def _canonical(gateway_name: str) -> str | None:
    """Map a gateway tool name (possibly namespaced, e.g. 'utils__text_normalize') to an allow-listed name."""
    norm = _normalize(gateway_name)
    for name in ALLOWED_UTILS_TOOLS:
        if norm == name or norm.endswith(f"_{name}"):
            return name
    return None


def _parse(raw: Any) -> Any:
    if isinstance(raw, tuple):  # (content, artifact)
        raw = raw[0]
    if isinstance(raw, list):  # content blocks
        raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def wrap_utils_tool(tool: BaseTool, name: str) -> BaseTool:
    """Expose an MCP tool under its canonical name, returning the Deep Agent envelope."""

    async def _call(**kwargs: Any) -> str:
        start = time.perf_counter()
        try:
            payload = _parse(await tool.ainvoke(kwargs))
        except Exception as exc:
            logger.warning("Utils tool %s failed: %s", name, exc)
            return fail("EXTERNAL_TOOL_ERROR", f"{name} failed: {exc}")
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if isinstance(payload, dict) and "error" in payload and ("status_code" in payload or len(payload) == 1):
            return fail("EXTERNAL_TOOL_ERROR", str(payload.get("response_text") or payload["error"]),
                        status_code=payload.get("status_code"), latency_ms=elapsed_ms)
        logger.debug("Utils tool %s answered in %d ms", name, elapsed_ms)
        return ok(payload)

    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"[Utils-Server] {tool.description}",
        args_schema=tool.args_schema,
    )


def allocate(tools: list[BaseTool]) -> dict[str, list[BaseTool]]:
    """Pick the allow-listed gateway tools and hand each agent its share."""
    by_name: dict[str, BaseTool] = {}
    for tool in tools:
        name = _canonical(tool.name)
        if name and name not in by_name:
            by_name[name] = wrap_utils_tool(tool, name)
    missing = ALLOWED_UTILS_TOOLS - set(by_name)
    if missing:
        logger.warning("Utils-Server tools not available on the gateway (pending approval?): %s", sorted(missing))
    return {agent: [by_name[n] for n in names if n in by_name] for agent, names in UTILS_TOOL_ALLOCATION.items()}


def gateway_connection() -> dict[str, Any] | None:
    if not settings.JARVIS_GATEWAY_URL:
        return None
    connection: dict[str, Any] = {
        "transport": "streamable_http",
        "url": f"{settings.JARVIS_GATEWAY_URL.rstrip('/')}/mcp/{settings.JARVIS_MCP_NAMESPACE}/",
        "timeout": timedelta(seconds=settings.UTILS_MCP_TIMEOUT_S),
    }
    if settings.JARVIS_API_KEY:
        connection["headers"] = {"Authorization": f"ApiKey {settings.JARVIS_API_KEY}"}
    return connection


async def load_utils_tools() -> dict[str, list[BaseTool]]:
    """Load and allocate the curated Utils-Server tools; empty allocation when unavailable."""
    empty: dict[str, list[BaseTool]] = {agent: [] for agent in UTILS_TOOL_ALLOCATION}
    connection = gateway_connection()
    if connection is None:
        logger.info("JARVIS_GATEWAY_URL not set — Utils-Server tools disabled")
        return empty
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({"jarvis": connection})
        tools = await asyncio.wait_for(client.get_tools(), timeout=settings.UTILS_MCP_TIMEOUT_S)
    except Exception as exc:
        logger.warning("Utils-Server tools unavailable (%s: %s) — continuing without them", type(exc).__name__, exc)
        return empty
    allocation = allocate(tools)
    logger.info("Loaded Utils-Server tools: %s", {k: [t.name for t in v] for k, v in allocation.items() if v})
    return allocation
