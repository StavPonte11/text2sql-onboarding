"""The agents and LLM models an evaluation can run with."""

import json
import logging

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings

logger = logging.getLogger(__name__)

# Evaluation agent → the agent MCP tool that answers for it. "previous" is the
# earlier TextToSQL agent, called by the evaluation service on its own server.
AGENT_TOOLS = {"current": "chat_with_agent", "deep": "deep_agent"}


def _agents(tools: dict[str, dict] | None, reason: str | None = None) -> dict:
    """
    Per agent: whether this agent server offers it and whether it takes a
    model, read from its tools (None: the tools could not be read).
    """
    agents = {}
    for agent, tool in AGENT_TOOLS.items():
        schema = (tools or {}).get(tool)
        agents[agent] = {
            "available": schema is not None
            if tools is not None
            else agent == "current",
            "supports_model": bool(
                schema and "model" in (schema.get("properties") or {})
            ),
            "reason": None
            if schema is not None
            else reason
            or f"This agent version has no {tool} tool (it comes with "
            "feature/deep-agent-updated).",
        }
    agents["previous"] = {"available": True, "supports_model": False, "reason": None}
    return agents


async def list_agent_models() -> dict:
    """
    {"models", "default", "list_error", "agents"}: the models from the agent's
    list_models tool (what its LLM endpoint serves), and which agents this
    agent server offers. An agent version without list_models, or one that
    can't be reached, lists no models and says why; evaluations then run on
    the agent's own model, named by AGENT_LLM_MODEL when it is set (a copy of
    the agent's LLM_MODEL, not read from the agent).
    """
    known_default = settings.AGENT_LLM_MODEL or None
    try:
        async with streamablehttp_client(f"{settings.AGENT_URL}/mcp") as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                tools = {tool.name: tool.inputSchema or {} for tool in listed.tools}
                if "list_models" not in tools:
                    return {
                        "models": [known_default] if known_default else [],
                        "default": known_default,
                        "list_error": "This agent version can't switch models; "
                        + (
                            f"it runs on {known_default} (AGENT_LLM_MODEL)."
                            if known_default
                            else "it runs on its default (set AGENT_LLM_MODEL "
                            "on the backend to name it)."
                        ),
                        "agents": _agents(tools),
                    }
                result = await session.call_tool("list_models", {})
                data = json.loads(result.content[0].text) if result.content else {}
    except Exception as e:  # the picker says why; runs still use the default
        logger.warning(f"[Eval] Could not list the agent's models: {e}")
        return {
            "models": [known_default] if known_default else [],
            "default": known_default,
            "list_error": f"Could not reach the agent to list its models: {e}",
            "agents": _agents(None, "Could not reach the agent to check."),
        }
    return {
        "models": [str(m) for m in data.get("models") or []],
        "default": data.get("default") or known_default,
        "list_error": data.get("list_error") or data.get("error"),
        "agents": _agents(tools),
    }
