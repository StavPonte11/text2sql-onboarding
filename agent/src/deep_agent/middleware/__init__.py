"""
Cross-cutting middleware (runs around every model call).

- catalog.py      loads the connection's catalog once per turn and appends it to the system prompt
- tool_filter.py  hides deepagents built-in tools we don't use

Middleware that belongs to one concern lives with it: the answer pipeline in deep_agent.answer.middleware,
the reviewer in deep_agent.validator.modes, the request context and date in deep_agent.context.
"""
