import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import trino.exceptions

import core.trino as core_trino
from core.trino import TrinoExecutionResult
from deep_agent import tools, trino_errors

FIXTURES = json.loads((Path(__file__).parent.parent / "fixtures" / "trino_errors.json").read_text(encoding="utf-8"))


def _failing_connection(exc: Exception) -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = exc
    return conn


def _run_through_core(error: dict) -> TrinoExecutionResult:
    exc = trino.exceptions.TrinoUserError(error, query_id="20261020_000000_00001_abcde")
    with patch.object(core_trino.settings, "TRINO_ENABLED", True), \
         patch.object(core_trino, "get_trino_connection", return_value=_failing_connection(exc)):
        return core_trino.execute_query_sync("SELECT 1")


@pytest.mark.parametrize("case", FIXTURES, ids=[c["error"].get("errorName", "?") + "->" + c["expected"] for c in FIXTURES])
def test_real_trino_payloads_are_classified_from_error_name(case):
    res = _run_through_core(case["error"])
    assert res.success is False
    assert res.error_name == case["error"]["errorName"]
    assert res.error_message == case["error"]["message"]  # clean message, not the exception repr
    assert res.query_id == "20261020_000000_00001_abcde"

    classified = trino_errors.classify_result(res)
    assert classified.category == case["expected"]
    assert classified.hint == trino_errors.HINTS[case["expected"]]


def test_error_location_is_reported():
    case = next(c for c in FIXTURES if c["error"]["errorName"] == "TYPE_MISMATCH")
    out = trino_errors.classify_result(_run_through_core(case["error"])).to_error()
    assert out["location"] == {"line": 3, "column": 5}
    assert out["trino_error_name"] == "TYPE_MISMATCH"


def test_connection_failure_is_infrastructure_not_sql():
    exc = ConnectionError("HTTPConnectionPool(host='localhost', port=8080): Max retries exceeded with url: /v1/statement")
    with patch.object(core_trino.settings, "TRINO_ENABLED", True), \
         patch.object(core_trino, "get_trino_connection", return_value=_failing_connection(exc)):
        res = core_trino.execute_query_sync("SELECT 1")
    assert res.error_name is None
    assert trino_errors.classify_result(res).category == "INFRASTRUCTURE_ERROR"


@pytest.mark.parametrize("message, expected", [
    ("line 1:8: Function 'st_geomfromtext' not registered", "FUNCTION_NOT_FOUND"),
    ("line 1:8: Unexpected parameters (varchar) for function length", "FUNCTION_SIGNATURE"),
    ("Division by zero", "DIVISION_BY_ZERO"),
    ("line 1:8: Column 'x' cannot be resolved", "COLUMN_NOT_FOUND"),
    ("line 1:15: Table 'a.b.c' does not exist", "TABLE_NOT_FOUND"),
    ("something unexpected happened", "EXECUTION_ERROR"),
])
def test_message_fallback_when_no_error_name(message, expected):
    assert trino_errors.categorize(None, None, message) == expected


def test_execute_tool_returns_structured_error():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:8: Function 'st_geomfromtext' not registered",
        error_name="FUNCTION_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 8), query_id="q1",
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT ST_GeomFromText('POINT(1 1)')"}))
    assert out["ok"] is False and out["data"] is None
    assert out["error"]["category"] == "FUNCTION_NOT_FOUND"
    assert out["error"]["location"] == {"line": 1, "column": 8}
    assert out["error"]["query_id"] == "q1"


def test_validate_returns_structured_json():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:8: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 8),
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql_syntax.invoke({"sql": "SELECT x FROM t"}))
    assert out["ok"] is False
    assert out["error"]["category"] == "COLUMN_NOT_FOUND"
