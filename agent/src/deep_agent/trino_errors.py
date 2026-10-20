"""
Map Trino failures to agent-facing error categories and retry hints.

Trino reports structured errors (errorName / errorType) for every query failure; we use
those first and fall back to message heuristics only for non-Trino exceptions
(connection failures, client-side errors).
"""

from dataclasses import dataclass

from core.trino import TrinoExecutionResult
from deep_agent.envelope import error_payload

HINTS: dict[str, str] = {
    "SYNTAX_ERROR": "Fix the specific syntax at the reported line/column — do not redesign the entire query.",
    "TABLE_NOT_FOUND": "Re-verify the exact 3-part table name from get_database_catalog.",
    "COLUMN_NOT_FOUND": "Re-verify the column name from get_table_columns for this table.",
    "AMBIGUOUS_COLUMN": "Qualify the column with its table alias (e.g. f.city_code).",
    "TYPE_MISMATCH": "Add CAST(column AS target_type) for the mismatched operand.",
    "FUNCTION_NOT_FOUND": "Check Trino function spelling. E.g., use ST_GeometryFromText, not ST_GeomFromText.",
    "FUNCTION_SIGNATURE": "The function exists but the argument types don't match — CAST the arguments to the expected types.",
    "GROUP_BY_ERROR": "Add every non-aggregated SELECT column to GROUP BY, or wrap it in an aggregate.",
    "DIVISION_BY_ZERO": "Wrap the divisor in NULLIF(divisor, 0).",
    "INVALID_VALUE": "A value could not be parsed/cast — use TRY_CAST / try() or fix the format string.",
    "TIMEOUT_OR_RESOURCE": "Add a tighter filter or LIMIT, select fewer columns, or pre-aggregate in a CTE.",
    "INFRASTRUCTURE_ERROR": "Infrastructure problem, not a SQL problem — do NOT rewrite the SQL. Retry once; if it fails again, report it.",
    "FORBIDDEN_STATEMENT": "Only a single read-only SELECT/WITH (or SHOW/DESCRIBE) statement without comments is allowed.",
    "EXECUTION_ERROR": "Investigate the specific error message.",
}

_BY_ERROR_NAME: dict[str, str] = {
    "SYNTAX_ERROR": "SYNTAX_ERROR",
    "TABLE_NOT_FOUND": "TABLE_NOT_FOUND",
    "SCHEMA_NOT_FOUND": "TABLE_NOT_FOUND",
    "CATALOG_NOT_FOUND": "TABLE_NOT_FOUND",
    "MISSING_CATALOG_NAME": "TABLE_NOT_FOUND",
    "MISSING_SCHEMA_NAME": "TABLE_NOT_FOUND",
    "COLUMN_NOT_FOUND": "COLUMN_NOT_FOUND",
    "MISSING_COLUMN_NAME": "COLUMN_NOT_FOUND",
    "AMBIGUOUS_NAME": "AMBIGUOUS_COLUMN",
    "TYPE_MISMATCH": "TYPE_MISMATCH",
    "INVALID_CAST_ARGUMENT": "INVALID_VALUE",
    "INVALID_FUNCTION_ARGUMENT": "INVALID_VALUE",
    "INVALID_LITERAL": "INVALID_VALUE",
    "FUNCTION_NOT_FOUND": "FUNCTION_NOT_FOUND",
    "EXPRESSION_NOT_AGGREGATE": "GROUP_BY_ERROR",
    "MUST_BE_AGGREGATE_OR_GROUP_BY": "GROUP_BY_ERROR",
    "INVALID_GROUPING_EXPRESSION": "GROUP_BY_ERROR",
    "DIVISION_BY_ZERO": "DIVISION_BY_ZERO",
    "NUMERIC_VALUE_OUT_OF_RANGE": "INVALID_VALUE",
    "EXCEEDED_TIME_LIMIT": "TIMEOUT_OR_RESOURCE",
    "EXCEEDED_CPU_LIMIT": "TIMEOUT_OR_RESOURCE",
    "EXCEEDED_SCAN_LIMIT": "TIMEOUT_OR_RESOURCE",
    "QUERY_REJECTED": "TIMEOUT_OR_RESOURCE",
    "PERMISSION_DENIED": "FORBIDDEN_STATEMENT",
}

_BY_ERROR_TYPE: dict[str, str] = {
    "INSUFFICIENT_RESOURCES": "TIMEOUT_OR_RESOURCE",
    "INTERNAL_ERROR": "INFRASTRUCTURE_ERROR",
    "EXTERNAL": "INFRASTRUCTURE_ERROR",
}

# Ordered fallback heuristics for errors without an errorName (most specific first).
_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("INFRASTRUCTURE_ERROR", ("max retries exceeded", "connection refused", "connection reset",
                              "failed to establish", "name or service not known", "trino disabled")),
    ("FUNCTION_SIGNATURE", ("unexpected parameters",)),
    ("FUNCTION_NOT_FOUND", ("not registered", "function not found", "unknown function")),
    ("GROUP_BY_ERROR", ("must be an aggregate expression or appear in group by",)),
    ("DIVISION_BY_ZERO", ("division by zero",)),
    ("AMBIGUOUS_COLUMN", ("is ambiguous",)),
    ("COLUMN_NOT_FOUND", ("cannot be resolved",)),
    ("TABLE_NOT_FOUND", ("table", "schema", "catalog")),  # combined with "does not exist" below
    ("TYPE_MISMATCH", ("cannot apply operator", "type mismatch", "cannot be applied to")),
    ("INVALID_VALUE", ("cannot be cast", "invalid format", "value cannot")),
    ("SYNTAX_ERROR", ("mismatched input", "extraneous input", "syntax error", "parse error")),
    ("TIMEOUT_OR_RESOURCE", ("exceeded", "timeout", "query killed", "memory limit")),
]


@dataclass(frozen=True)
class ClassifiedError:
    category: str
    message: str
    hint: str
    error_name: str | None = None
    error_location: tuple[int, int] | None = None
    query_id: str | None = None

    def to_error(self) -> dict:
        """The `error` object of the tool envelope."""
        return error_payload(
            self.category,
            self.message,
            self.hint,
            location={"line": self.error_location[0], "column": self.error_location[1]} if self.error_location else None,
            trino_error_name=self.error_name,
            query_id=self.query_id,
        )


def _from_message(message: str) -> str:
    msg = message.lower()
    for category, keywords in _KEYWORD_RULES:
        if category == "TABLE_NOT_FOUND":
            if "does not exist" in msg and any(k in msg for k in keywords):
                return category
            continue
        if any(k in msg for k in keywords):
            return category
    return "EXECUTION_ERROR"


def categorize(error_name: str | None, error_type: str | None, message: str) -> str:
    if error_name == "FUNCTION_NOT_FOUND" and "unexpected parameters" in message.lower():
        return "FUNCTION_SIGNATURE"
    if error_name and error_name in _BY_ERROR_NAME:
        return _BY_ERROR_NAME[error_name]
    if error_name and error_name.startswith("EXCEEDED_"):
        return "TIMEOUT_OR_RESOURCE"
    if error_type and error_type in _BY_ERROR_TYPE:
        return _BY_ERROR_TYPE[error_type]
    return _from_message(message)


def classify_result(res: TrinoExecutionResult) -> ClassifiedError:
    message = res.error_message or "Unknown error"
    category = categorize(res.error_name, res.error_type, message)
    return ClassifiedError(
        category=category,
        message=message,
        hint=HINTS[category],
        error_name=res.error_name,
        error_location=tuple(res.error_location) if res.error_location else None,
        query_id=res.query_id,
    )


def classify_message(category: str | None, message: str) -> ClassifiedError:
    """Classify a failure that never produced a TrinoExecutionResult (exceptions, guard rejections)."""
    category = category or _from_message(message)
    return ClassifiedError(category=category, message=message, hint=HINTS.get(category, HINTS["EXECUTION_ERROR"]))
