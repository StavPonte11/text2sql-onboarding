"""
Deterministic catalog loading for the Deep Agent.

Every question needs the connection's catalog, so it is not a tool the model has to remember to
call: CatalogMiddleware loads it once at the start of each run (for the request's connection,
through the Jeen client's TTL cache) into graph state, and adds it to the system message of
every model call as a "Database Catalog" section after the static prompt. It is never written
into the message history, so it is not duplicated across turns.

Without a catalog the agent cannot answer, so a failed load fails the run with a clear error.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from deep_agent.context import DeepAgentRuntimeState

CATALOG_HEADER = "\n\n---\n\n# Database Catalog\n\nThe complete catalog of the connection you are answering against:\n\n"


class CatalogLoadError(RuntimeError):
    """The connection's catalog could not be loaded; the run cannot proceed without it."""


async def load_catalog(connection_id: int | None) -> str:
    try:
        catalog = await get_jeen_metadata_client().get_catalog_prompt(connection_id=connection_id)
    except Exception as exc:
        raise CatalogLoadError(f"Could not load the database catalog for connection {connection_id}: {exc}") from exc
    if not catalog or not str(catalog).strip():
        raise CatalogLoadError(f"The database catalog for connection {connection_id} is empty")
    return str(catalog)


def _connection_id(runtime: Any) -> int | None:
    return getattr(getattr(runtime, "context", None), "connection_id", None)


def _with_catalog(request: ModelRequest) -> ModelRequest:
    catalog = (request.state or {}).get("jeen_catalog")
    if not catalog or request.system_message is None:
        return request
    content = request.system_message.content
    if isinstance(content, str):
        new_content: Any = content + CATALOG_HEADER + catalog
    else:
        new_content = [*content, {"type": "text", "text": CATALOG_HEADER + catalog}]
    return request.override(system_message=SystemMessage(content=new_content))


class CatalogMiddleware(AgentMiddleware):
    """Load the connection's catalog at the start of each run and show it to every model call."""

    state_schema = DeepAgentRuntimeState

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"jeen_catalog": await load_catalog(_connection_id(runtime))}

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"jeen_catalog": asyncio.run(load_catalog(_connection_id(runtime)))}

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(_with_catalog(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(_with_catalog(request))
