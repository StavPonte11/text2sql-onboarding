"""
Request-scoped runtime context and state for the Deep Agent.

Nothing request-specific lives in module globals:
- DeepAgentContext is passed per invocation (`agent.ainvoke(..., context=DeepAgentContext(...))`)
  and read by tools through `ToolRuntime.context`.
- The connection's catalog is loaded once per run by CatalogMiddleware (deep_agent.catalog)
  into graph state (`jeen_catalog`) and read back by tools through `ToolRuntime.state`.
- Today's date is added to the latest human message on every model call, in the
  context's timezone, so long-running processes never serve a stale date.
"""

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


def merge_executions(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Append-only reducer for executed_queries that ignores records already present.

    deepagents passes the parent's state into a subagent and returns the subagent's state as
    an update; a plain list-append reducer would duplicate the parent's history on every
    `task` call.
    """
    merged = list(left or [])
    seen = {(r.get("query_id"), r.get("executed_sql")) for r in merged}
    for record in right or []:
        key = (record.get("query_id"), record.get("executed_sql"))
        if key not in seen:
            merged.append(record)
            seen.add(key)
    return merged


def latest(left: Any, right: Any) -> Any:
    """Last write wins. deepagents returns a subagent's whole state (minus messages) to the parent, so
    parallel `task` calls write these keys in the same step; a plain key would reject that."""
    return right


def merge_reviews(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Reducer for semantic_validations: records are merged by their `id`; `None` resets the list.

    A subagent returns the list it was given plus its own record, and parallel reviews return in the
    same step, so a plain value would either duplicate records or reject the concurrent writes.
    """
    if right is None:
        return []
    merged = list(left or [])
    seen = {r.get("id") for r in merged}
    for record in right:
        if record.get("id") not in seen:
            merged.append(record)
            seen.add(record.get("id"))
    return merged


class DeepAgentRuntimeState(AgentState):
    jeen_catalog: NotRequired[Annotated[str, latest]]  # the connection's catalog, loaded once per run (deep_agent.catalog)
    executed_queries: NotRequired[Annotated[list[dict[str, Any]], merge_executions]]  # successful executions
    final_result: NotRequired[Annotated[dict[str, Any], latest]]  # verified answer + rows (see deep_agent.answer)
    turn_execution_start: NotRequired[Annotated[int, latest]]  # index into executed_queries where the current turn began
    semantic_validations: NotRequired[Annotated[list[dict[str, Any]], merge_reviews]]  # this turn's reviews (deep_agent.validator)


# Name of the one-time "submit now" message (deep_agent.answer.SubmitReminderMiddleware): not the user's words.
REMINDER_NAME = "submit_reminder"


def is_user_message(msg: Any) -> bool:
    """A message the user wrote (not the harness's submit reminder)."""
    return isinstance(msg, HumanMessage) and getattr(msg, "name", None) != REMINDER_NAME


def connection_id(runtime: ToolRuntime | None) -> int | None:
    ctx = getattr(runtime, "context", None)
    return getattr(ctx, "connection_id", None)


def cached_catalog(runtime: ToolRuntime | None) -> str | None:
    state = getattr(runtime, "state", None) or {}
    return state.get("jeen_catalog") or None


def user_request(runtime: ToolRuntime | None) -> str:
    """Every human message in the thread, oldest first, one per line.

    A reply to a clarifying question arrives as a new human message on the same thread, so the
    request being answered is the original question plus those replies — not just the last one.
    """
    state = getattr(runtime, "state", None) or {}
    texts = [msg.text for msg in state.get("messages") or [] if is_user_message(msg) and msg.text.strip()]
    return "\n".join(texts)


def _now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def date_line(tz: str) -> str:
    now = _now(tz)
    return f"Today's date: {now.date().isoformat()} ({now.strftime('%A')}, timezone {tz})."


def _with_date(request: ModelRequest) -> ModelRequest:
    """Prefix the latest user message with today's date for this model call only.

    The date is deliberately *not* put in the system message: deepagents appends its
    skills section after user middleware, so a date there would sit mid-prompt and invalidate
    provider prompt caching for everything after it every day. Here the system prompt stays
    byte-identical and the change is transient (never written to graph state).
    """
    ctx = getattr(request.runtime, "context", None)
    line = date_line(getattr(ctx, "timezone", None) or DEFAULT_TIMEZONE)
    messages = list(request.messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if is_user_message(msg):
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
