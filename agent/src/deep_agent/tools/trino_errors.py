"""
Error objects returned to the agent when SQL fails (the `error` of the tool envelope).

Trino errors are passed through as Trino reported them (errorName, errorType, message,
location, query id) — no re-labelling. Two failures never get a Trino error and are
named here instead:

  FORBIDDEN_STATEMENT  the SQL guard rejected the statement before it reached Trino
  CONNECTION_ERROR     Trino could not be reached / returned no structured error
"""

from core.trino import TrinoExecutionResult
from deep_agent.tools.envelope import error

FORBIDDEN_STATEMENT = "FORBIDDEN_STATEMENT"
CONNECTION_ERROR = "CONNECTION_ERROR"


def from_result(res: TrinoExecutionResult) -> dict:
    """Error object for a failed TrinoExecutionResult."""
    if res.error_name is None:
        return error(CONNECTION_ERROR, res.error_message or "Unknown error")
    location = {"line": res.error_location[0], "column": res.error_location[1]} if res.error_location else None
    return error(
        res.error_name,
        res.error_message or "",
        type=res.error_type,
        location=location,
        query_id=res.query_id,
    )


def forbidden(message: str) -> dict:
    """Error object for SQL the guard rejected."""
    return error(FORBIDDEN_STATEMENT, message)


def from_exception(exc: Exception) -> dict:
    """Error object for an exception raised while calling Trino."""
    return error(CONNECTION_ERROR, str(exc))
