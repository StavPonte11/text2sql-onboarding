"""
Error payloads returned to the agent when SQL fails.

Trino errors are passed through as Trino reported them (errorName, errorType, message,
location, query id) — no re-labelling. Two failures never get a Trino error and are
named here instead:

  FORBIDDEN_STATEMENT  the SQL guard rejected the statement before it reached Trino
  CONNECTION_ERROR     Trino could not be reached / returned no structured error
"""

from core.trino import TrinoExecutionResult

FORBIDDEN_STATEMENT = "FORBIDDEN_STATEMENT"
CONNECTION_ERROR = "CONNECTION_ERROR"


def from_result(res: TrinoExecutionResult) -> dict:
    """Error payload for a failed TrinoExecutionResult."""
    if res.error_name is None:
        return _payload(CONNECTION_ERROR, None, res.error_message or "Unknown error")
    return _payload(res.error_name, res.error_type, res.error_message or "", res.error_location, res.query_id)


def forbidden(message: str) -> dict:
    """Error payload for SQL the guard rejected."""
    return _payload(FORBIDDEN_STATEMENT, None, message)


def from_exception(exc: Exception) -> dict:
    """Error payload for an exception raised while calling Trino."""
    return _payload(CONNECTION_ERROR, None, str(exc))


def _payload(
    name: str,
    error_type: str | None,
    message: str,
    location: tuple[int, int] | None = None,
    query_id: str | None = None,
) -> dict:
    out: dict = {"success": False, "error_name": name, "error_type": error_type, "error_message": message}
    if location:
        out["error_location"] = {"line": location[0], "column": location[1]}
    if query_id:
        out["query_id"] = query_id
    return out
