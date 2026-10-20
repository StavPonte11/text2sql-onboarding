"""
The single return contract for every Deep Agent tool:

    {"ok": true,  "data": <payload>, "error": null}
    {"ok": false, "data": null,      "error": {"category", "message", "hint", ...details}}

Serialised compactly (no indentation whitespace, raw Unicode) to keep tool output tokens low.
"""

import json
from typing import Any

HINTS: dict[str, str] = {
    "METADATA_ERROR": "The metadata service failed — do NOT conclude the data is empty. Retry once, or continue with other tools.",
    "NOT_FOUND": "Nothing matched. Re-check the exact name from get_database_catalog / get_database_tables.",
    "INVALID_INPUT": "Fix the tool arguments as described in the message and call again.",
    "ENRICHMENT_ERROR": "Enrichment failed; the original SQL is unchanged — verify filter values with search_column_values instead.",
    "LOCATION_NOT_FOUND": "The location could not be geocoded. Try the Hebrew or English official name, or filter by a text column instead.",
}


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def ok(data: Any) -> str:
    return _dumps({"ok": True, "data": data, "error": None})


def error_payload(category: str, message: str, hint: str | None = None, **details: Any) -> dict:
    err = {"category": category, "message": message, "hint": hint or HINTS.get(category, "Investigate the error message.")}
    err.update({k: v for k, v in details.items() if v is not None})
    return err


def fail(category: str, message: str, hint: str | None = None, **details: Any) -> str:
    return _dumps({"ok": False, "data": None, "error": error_payload(category, message, hint, **details)})
