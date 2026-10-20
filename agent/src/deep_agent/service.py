"""
Serving the Deep Agent behind the existing `chat_with_agent` MCP tool.

- One agent per process (built lazily, shared by all requests); per-request data travels in
  DeepAgentContext (connection, user) and the thread id.
- Durable threads: AsyncPostgresSaver when DEEP_AGENT_CHECKPOINT_DB_URL is set (shared by all
  replicas, survives restarts), otherwise an in-memory saver.
- Streaming: the same Redis events the legacy graph publishes (`agent_stream:{thread_id}`,
  {"thread_id", "node", "status"}), so the backend SSE relay and UI work unchanged. Nodes are
  named `deep:<tool>` (e.g. deep:execute_trino_query) plus `deep:answer`.
- Response: the legacy JSON contract (status / summary / sql_query / sql_explanation /
  interrupt_details / trace_id / execution_path / is_unanswerable) plus `engine`,
  `verification` and `confidence`.
- Clarification: a DeepAgentAnswer with needs_clarification returns status "interrupted" with
  interrupt_details {"type": "hitl_escalation", "subtype": "clarification", "reason": <question>},
  which the UI already renders as a free-text prompt. The reply (resume_value) is sent as the
  next user message on the same thread.
"""

import asyncio
import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.checkpoint.memory import MemorySaver

from agent.config import settings
from agent.utils.redis_publisher import publish_node_event
from deep_agent import observability
from deep_agent.answer import best_effort_result, render_markdown
from deep_agent.context import DeepAgentContext

logger = logging.getLogger(__name__)

_agent = None
_agent_lock: asyncio.Lock | None = None
_pg_pool = None

UNANSWERED = {"no_answer", "rejected", "re_execution_failed"}


async def build_checkpointer():
    """AsyncPostgresSaver over a process-wide pool when configured, else MemorySaver."""
    global _pg_pool
    if not settings.DEEP_AGENT_CHECKPOINT_DB_URL:
        logger.warning("DEEP_AGENT_CHECKPOINT_DB_URL not set — deep-agent threads are in-memory (lost on restart)")
        return MemorySaver()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    _pg_pool = AsyncConnectionPool(
        settings.DEEP_AGENT_CHECKPOINT_DB_URL, open=False, max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await _pg_pool.open()
    saver = AsyncPostgresSaver(_pg_pool)
    await saver.setup()  # idempotent: creates/migrates the checkpoint tables
    return saver


async def get_deep_agent():
    """The process-wide Deep Agent (built once, on first use)."""
    global _agent, _agent_lock
    if _agent is not None:
        return _agent
    if _agent_lock is None:
        _agent_lock = asyncio.Lock()
    async with _agent_lock:
        if _agent is None:
            from deep_agent.agent import acreate_deep_agent

            _agent = await acreate_deep_agent(checkpointer=await build_checkpointer())
    return _agent


def reset_for_tests(agent=None) -> None:
    global _agent, _agent_lock
    _agent, _agent_lock = agent, None


class StreamPublisher(AsyncCallbackHandler):
    """Publishes tool start/end to the legacy Redis stream and records the execution path."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.execution_path: list[str] = []
        self._names: dict[UUID, str] = {}

    async def _publish(self, node: str, status: str) -> None:
        await publish_node_event(self.thread_id, node, status)

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> None:
        name = f"deep:{(serialized or {}).get('name') or kwargs.get('name') or 'tool'}"
        self._names[run_id] = name
        self.execution_path.append(name)
        await self._publish(name, "active")

    async def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        await self._publish(self._names.pop(run_id, "deep:tool"), "completed")

    async def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        await self._publish(self._names.pop(run_id, "deep:tool"), "error")


def to_response(thread_id: str, final_result: dict[str, Any], trace_id: str | None,
                execution_path: list[str]) -> dict[str, Any]:
    answer = final_result.get("answer") or {}
    status = final_result.get("status", "no_answer")
    base = {
        "thread_id": thread_id,
        "engine": "deep",
        "verification": status,
        "confidence": answer.get("confidence"),
        "trace_id": trace_id,
        "execution_path": execution_path,
    }
    if status == "clarification":
        return {
            **base,
            "status": "interrupted",
            "interrupt_details": {
                "type": "hitl_escalation",
                "subtype": "clarification",
                "reason": answer.get("needs_clarification") or answer.get("explanation_he") or "",
            },
            "sql_query": None,
        }
    return {
        **base,
        "status": "completed",
        "summary": render_markdown({**final_result, "sql": None}),  # SQL is returned separately
        "raw_data_ref": None,
        "sql_query": final_result.get("sql"),
        "sql_explanation": answer.get("explanation_he"),
        "is_unanswerable": status in UNANSWERED,
    }


def _resume_text(resume_value: str | dict | None) -> str | None:
    if isinstance(resume_value, str):
        return resume_value
    if isinstance(resume_value, dict):
        return resume_value.get("feedback") or resume_value.get("answer")
    return None


async def run_deep_chat(
    *,
    connection_id: int | None,
    query: str | None = None,
    thread_id: str,
    resume_value: str | dict | None = None,
    user_id: str | None = None,
    agent=None,
) -> dict[str, Any]:
    """One chat turn on the Deep Agent, in the legacy chat_with_agent response shape."""
    message = query if resume_value is None else _resume_text(resume_value)
    if not message:
        return {"error": "Query is required for new chat session." if resume_value is None
                else "resume_value must be the user's answer (a string or {'feedback': ...})."}

    agent = agent or await get_deep_agent()
    context = DeepAgentContext(connection_id=connection_id, user_id=user_id)
    publisher = StreamPublisher(thread_id)
    config = observability.build_run_config(thread_id, user_id=user_id, connection_id=connection_id,
                                            callbacks=[publisher])
    start = time.perf_counter()
    try:
        async with asyncio.timeout(settings.DEEP_AGENT_RUN_TIMEOUT_S):
            state = await agent.ainvoke({"messages": [("user", message)]}, config, context=context)
        final_result = state.get("final_result") or {"status": "no_answer", "verified": False}
    except TimeoutError:
        state = (await agent.aget_state(config)).values
        final_result = best_effort_result(state.get("executed_queries") or [],
                                          f"timeout after {settings.DEEP_AGENT_RUN_TIMEOUT_S:.0f}s")

    await publish_node_event(thread_id, "deep:answer", "completed")
    trace_id = observability.langfuse_trace_id(config)
    observability.score_run(trace_id, observability.run_metrics(state.get("messages") or [],
                                                               time.perf_counter() - start, final_result))
    observability.flush()
    return to_response(thread_id, final_result, trace_id, publisher.execution_path)
