"""
The single return contract for every Deep Agent tool:

    {"ok": true,  "data": <payload>, "error": null}
    {"ok": false, "data": null,      "error": {"name", "message", ...details}}

error.name is Trino's own errorName for SQL failures (see deep_agent.trino_errors), or one of
the names below for failures of our own tools.

Serialised compactly (no indentation whitespace, raw Unicode) to keep tool output tokens low.
"""

import json
from typing import Any

METADATA_ERROR = "METADATA_ERROR"          # the Jeen metadata service failed or returned an unexpected payload
NOT_FOUND = "NOT_FOUND"                    # the metadata service has nothing stored for the requested object
INVALID_INPUT = "INVALID_INPUT"            # the tool arguments could not be used
ENRICHMENT_ERROR = "ENRICHMENT_ERROR"      # filter enrichment failed; the SQL is unchanged
LOCATION_NOT_FOUND = "LOCATION_NOT_FOUND"  # the location could not be resolved to an area


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def ok(data: Any) -> str:
    return _dumps({"ok": True, "data": data, "error": None})


def error(name: str, message: str, **details: Any) -> dict:
    """An `error` object: name + message, plus any details that are not None."""
    return {"name": name, "message": message, **{k: v for k, v in details.items() if v is not None}}


def fail(err: dict) -> str:
    return _dumps({"ok": False, "data": None, "error": err})
