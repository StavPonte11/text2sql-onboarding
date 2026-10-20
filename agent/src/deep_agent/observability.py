"""
Langfuse tracing for the Deep Agent.

- build_run_config(): the one RunnableConfig every deep-agent run (CLI and API) uses —
  Langfuse CallbackHandler + session/user/tags metadata, so a run is exactly one trace.
- annotate_current_span(): structured metadata on the current tool observation. Only
  reliable for *sync* tools: LangChain runs the (sync) Langfuse handler for async tools in
  a copied context, so their span is not current inside the tool. Every tool therefore
  also carries its telemetry in its envelope output, which Langfuse always records.
- run_metrics() / score_run(): end-of-run scores on the trace.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from agent.config import settings

logger = logging.getLogger(__name__)

AGENT_NAME = "deep-agent"


def agent_version() -> str:
    """DEEP_AGENT_VERSION (e.g. the git SHA injected at build time), else the package version."""
    explicit = os.environ.get("DEEP_AGENT_VERSION")
    if explicit:
        return explicit
    try:
        return version("agent")
    except PackageNotFoundError:
        return "dev"


def _langfuse_handler() -> BaseCallbackHandler | None:
    from core.langfuse import get_langfuse_handler

    return get_langfuse_handler()


def build_run_config(
    thread_id: str,
    *,
    user_id: str | None = None,
    connection_id: int | None = None,
    callbacks: list[BaseCallbackHandler] | None = None,
    tracing: bool | None = None,
) -> RunnableConfig:
    """RunnableConfig for one deep-agent invocation: one Langfuse trace per run, grouped by thread."""
    handlers = list(callbacks or [])
    if tracing if tracing is not None else settings.DEEP_AGENT_TRACING_ENABLED:
        handler = _langfuse_handler()
        if handler is not None:
            handlers.append(handler)

    metadata: dict[str, Any] = {
        "langfuse_session_id": thread_id,
        "langfuse_tags": [AGENT_NAME],
        "agent": AGENT_NAME,
        "agent_version": agent_version(),
        "connection_id": connection_id if connection_id is not None else settings.JEEN_METADATA_CONNECTION_ID,
    }
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if handlers:
        from deep_agent.prompt_registry import load_prompt

        prompt = load_prompt("system")
        if prompt.client is not None:
            metadata["langfuse_prompt"] = prompt.client  # links generations to the served prompt version
        metadata["prompt_source"] = prompt.source
        metadata["prompt_version"] = prompt.version

    return RunnableConfig(
        configurable={"thread_id": thread_id},
        callbacks=handlers,
        metadata=metadata,
        tags=[AGENT_NAME],
        run_name=AGENT_NAME,
        recursion_limit=settings.DEEP_AGENT_RECURSION_LIMIT,
    )


def langfuse_trace_id(config: RunnableConfig) -> str | None:
    for handler in config.get("callbacks") or []:
        trace_id = getattr(handler, "last_trace_id", None)
        if trace_id:
            return trace_id
    return None


def annotate_current_span(metadata: dict[str, Any], level: str | None = None) -> None:
    """Best-effort metadata on the current Langfuse observation (no-op without an active span)."""
    if not settings.DEEP_AGENT_TRACING_ENABLED:
        return
    try:
        from agent.langfuse_client import langfuse_client

        langfuse_client.update_current_span(metadata=metadata, level=level)
    except Exception as exc:  # telemetry must never break a tool
        logger.debug("annotate_current_span skipped: %s", exc)


@dataclass(frozen=True)
class RunMetrics:
    model_calls: int
    tool_calls: int
    sql_executions: int
    executed_successfully: bool
    final_sql_present: bool
    latency_s: float
    answer_verified: bool = False


def _envelope_ok(text: str) -> bool | None:
    try:
        return bool(json.loads(text).get("ok"))
    except (ValueError, AttributeError):
        return None


def run_metrics(messages: list[BaseMessage], latency_s: float, final_result: dict | None = None) -> RunMetrics:
    ai = [m for m in messages if m.type == "ai"]
    executions = [m for m in messages if m.type == "tool" and m.name == "execute_trino_query"]
    final_text = ai[-1].text if ai else ""
    final_sql_present = bool(final_result.get("sql")) if final_result else "```sql" in final_text.lower()
    return RunMetrics(
        model_calls=len(ai),
        tool_calls=sum(len(getattr(m, "tool_calls", None) or []) for m in ai),
        sql_executions=len(executions),
        executed_successfully=any(_envelope_ok(m.text) for m in executions),
        final_sql_present=final_sql_present,
        latency_s=round(latency_s, 2),
        answer_verified=bool(final_result and final_result.get("verified")),
    )


def score_run(trace_id: str | None, metrics: RunMetrics) -> None:
    """Attach run-level scores to the Langfuse trace (no-op without a trace)."""
    if not trace_id or not settings.DEEP_AGENT_TRACING_ENABLED:
        return
    try:
        from agent.langfuse_client import langfuse_client

        for name, value in asdict(metrics).items():
            data_type = "BOOLEAN" if isinstance(value, bool) else "NUMERIC"
            langfuse_client.create_score(
                trace_id=trace_id, name=f"{AGENT_NAME}.{name}", value=float(value), data_type=data_type
            )
    except Exception as exc:
        logger.warning("score_run failed for trace %s: %s", trace_id, exc)


def flush() -> None:
    try:
        from agent.langfuse_client import langfuse_client

        langfuse_client.flush()
    except Exception as exc:
        logger.debug("langfuse flush skipped: %s", exc)
