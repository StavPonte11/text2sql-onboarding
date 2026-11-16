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
    the reason: evaluations then run on the agent's default model.
    """
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
                        "models": [],
                        "default": None,
                        "list_error": "This agent version can't choose a model; "
                        "it uses its default.",
                    }
                result = await session.call_tool("list_models", {})
                data = json.loads(result.content[0].text) if result.content else {}
    except Exception as e:  # the picker says why; runs still use the default
        logger.warning(f"[Eval] Could not list the agent's models: {e}")
        return {
            "models": [],
            "default": None,
            "list_error": f"Could not reach the agent to list its models: {e}",
        }
    return {
        "models": [str(m) for m in data.get("models") or []],
        "default": data.get("default"),
        "list_error": data.get("list_error") or data.get("error"),
    }
