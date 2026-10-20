"""
Request-scoped runtime context and state for the Deep Agent.

Nothing request-specific lives in module globals:
- DeepAgentContext is passed per invocation (`agent.ainvoke(..., context=DeepAgentContext(...))`)
  and read by tools through `ToolRuntime.context`.
- Connection capabilities and the raw catalog fetched by get_database_catalog are written to
  graph state (`connection_capabilities`, `jeen_catalog`) and read back by tools through
  `ToolRuntime.state`.
- Today's date is added to the latest human message on every model call, in the
  context's timezone, so long-running processes never serve a stale date.
"""

import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, NotRequired
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage

DEFAULT_TIMEZONE = "Asia/Jerusalem"


@dataclass(frozen=True)
class DeepAgentContext:
    connection_id: int | None = None   # Jeen metadata connection; None -> JEEN_METADATA_CONNECTION_ID
    user_id: str | None = None
    timezone: str = DEFAULT_TIMEZONE


class DeepAgentRuntimeState(AgentState):
    connection_capabilities: NotRequired[dict[str, bool]]
    jeen_catalog: NotRequired[str]  # raw catalog text, cached so tools never need the model to re-send it
    executed_queries: NotRequired[Annotated[list[dict[str, Any]], operator.add]]  # successful executions (append-only)
    final_result: NotRequired[dict[str, Any]]  # verified answer + rows (see deep_agent.answer)


def connection_id(runtime: ToolRuntime | None) -> int | None:
    ctx = getattr(runtime, "context", None)
    return getattr(ctx, "connection_id", None)


def capability(runtime: ToolRuntime | None, name: str) -> bool:
    """Whether the connection has a discovery source; unknown (catalog not fetched yet) counts as available."""
    state = getattr(runtime, "state", None) or {}
    return bool((state.get("connection_capabilities") or {}).get(name, True))


def cached_catalog(runtime: ToolRuntime | None) -> str | None:
    state = getattr(runtime, "state", None) or {}
    return state.get("jeen_catalog") or None


def latest_user_request(runtime: ToolRuntime | None) -> str:
    """Text of the most recent human message in the thread (the request currently being answered)."""
    state = getattr(runtime, "state", None) or {}
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage):
            return msg.text
    return ""


def _now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def date_line(tz: str) -> str:
    now = _now(tz)
    return f"Today's date: {now.date().isoformat()} ({now.strftime('%A')}, timezone {tz})."


def _with_date(request: ModelRequest) -> ModelRequest:
    """Prefix the latest human message with today's date for this model call only.

    The date is deliberately *not* put in the system message: deepagents appends memory
    (AGENTS.md) after user middleware, so a date there would sit mid-prompt and invalidate
    provider prompt caching for everything after it every day. Here the system prompt stays
    byte-identical and the change is transient (never written to graph state).
    """
    ctx = getattr(request.runtime, "context", None)
    line = date_line(getattr(ctx, "timezone", None) or DEFAULT_TIMEZONE)
    messages = list(request.messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, HumanMessage):
            content: Any = msg.content
            if isinstance(content, str):
                new_content: Any = f"[{line}]\n\n{content}"
            else:
                new_content = [{"type": "text", "text": f"[{line}]"}, *content]
            messages[i] = msg.model_copy(update={"content": new_content})
            return request.override(messages=messages)
    return request


class DeepAgentContextMiddleware(AgentMiddleware):
    """Registers the runtime state keys and gives every model call today's date."""

    state_schema = DeepAgentRuntimeState

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(_with_date(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(_with_date(request))
