"""
Hide deepagents built-in tools this agent never needs.

Every model call carries the schema of every bound tool; dropping these saves ~1k prompt
tokens per call and removes off-task options:
- execute: always errors without a sandbox backend
- write_file / edit_file: skills and memory are read-only; planning goes through write_todos
- glob / grep / ls: the skills are listed in the prompt with their paths and read with read_file
"""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

EXCLUDED_BUILTIN_TOOLS = frozenset({"execute", "write_file", "edit_file", "glob", "grep", "ls"})


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
