"""The LLM models the agent can run an evaluation with."""

import json
import logging

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings

logger = logging.getLogger(__name__)


async def list_agent_models() -> dict:
    """
    {"models": [...], "default": ..., "list_error": ...} from the agent's
    list_models tool (the models its LLM endpoint serves). An agent version
    without that tool, or one that can't be reached, gives an empty list and
    the reason: evaluations then run on the agent's default model, named by
    AGENT_LLM_MODEL when it is set.
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
                tools = await session.list_tools()
                if not any(tool.name == "list_models" for tool in tools.tools):
                    return {
                        "models": [known_default] if known_default else [],
                        "default": known_default,
                        "list_error": "This agent version can't switch models; "
                        + (
                            f"it runs on {known_default}."
                            if known_default
                            else "it runs on its default (set AGENT_LLM_MODEL "
                            "on the backend to name it)."
                        ),
                    }
                result = await session.call_tool("list_models", {})
                data = json.loads(result.content[0].text) if result.content else {}
    except Exception as e:  # the picker says why; runs still use the default
        logger.warning(f"[Eval] Could not list the agent's models: {e}")
        return {
            "models": [known_default] if known_default else [],
            "default": known_default,
            "list_error": f"Could not reach the agent to list its models: {e}",
        }
    return {
        "models": [str(m) for m in data.get("models") or []],
        "default": data.get("default") or known_default,
        "list_error": data.get("list_error") or data.get("error"),
    }
