"""
Loop bounds and tool-surface trimming for the Deep Agent.

- Hard caps on model calls, tool calls and SQL executions per run (langchain v1 middleware).
  Hitting the model-call cap ends the run; the answer middleware then returns the best
  answer so far (last successful query, confidence=low) instead of failing.
- Retries for transient model errors (with a short per-request timeout set on the LLM).
- BuiltinToolFilterMiddleware hides deepagents built-ins this agent never needs, so every
  model call carries fewer tool schemas and the model has fewer off-task options.
"""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)

from agent.config import settings

# deepagents built-ins with no use in a read-only SQL agent:
# - execute: always errors without a sandbox backend
# - write_file / edit_file: skills and memory are read-only; scratch notes go through write_todos
# - glob / grep: skills are listed in the prompt and read with read_file
EXCLUDED_BUILTIN_TOOLS = frozenset({"execute", "write_file", "edit_file", "glob", "grep"})


class BuiltinToolFilterMiddleware(AgentMiddleware):
    """Remove excluded built-in tools from every model request."""

    def __init__(self, excluded: frozenset[str] = EXCLUDED_BUILTIN_TOOLS) -> None:
        super().__init__()
        self.excluded = excluded

    def _filter(self, request: ModelRequest) -> ModelRequest:
        tools = [t for t in request.tools if getattr(t, "name", None) not in self.excluded]
        return request.override(tools=tools)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self._filter(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._filter(request))


def build_limit_middleware() -> list[AgentMiddleware]:
    return [
        ModelCallLimitMiddleware(run_limit=settings.DEEP_AGENT_MAX_MODEL_CALLS, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=settings.DEEP_AGENT_MAX_TOOL_CALLS),
        ToolCallLimitMiddleware(tool_name="execute_trino_query", run_limit=settings.DEEP_AGENT_MAX_SQL_EXECUTIONS),
        ModelRetryMiddleware(max_retries=settings.DEEP_AGENT_LLM_MAX_RETRIES, initial_delay=0.5, max_delay=5.0),
        BuiltinToolFilterMiddleware(),
    ]
