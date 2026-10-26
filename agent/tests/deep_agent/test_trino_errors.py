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


@pytest.mark.parametrize("error", FIXTURES, ids=[e["errorName"] for e in FIXTURES])
def test_trino_errors_are_passed_through_unchanged(error):
    res = _run_through_core(error)
    assert res.success is False
    assert res.error_message == error["message"]  # clean message, not the exception repr
    assert res.query_id == "20261020_000000_00001_abcde"

    out = trino_errors.from_result(res)
    assert out["error_name"] == error["errorName"]
    assert out["error_type"] == error["errorType"]
    assert out["error_message"] == error["message"]
    location = error.get("errorLocation")
    if location:
        assert out["error_location"] == {"line": location["lineNumber"], "column": location["columnNumber"]}
    else:
        assert "error_location" not in out


def test_connection_failure_is_reported_as_connection_error():
    exc = ConnectionError("HTTPConnectionPool(host='localhost', port=8080): Max retries exceeded with url: /v1/statement")
    with patch.object(core_trino.settings, "TRINO_ENABLED", True), \
         patch.object(core_trino, "get_trino_connection", return_value=_failing_connection(exc)):
        res = core_trino.execute_query_sync("SELECT 1")
    out = trino_errors.from_result(res)
    assert out["error_name"] == "CONNECTION_ERROR"
    assert out["error_type"] is None
    assert "Max retries exceeded" in out["error_message"]


def test_execute_tool_returns_trinos_error():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:8: Function 'st_geomfromtext' not registered",
        error_name="FUNCTION_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 8), query_id="q1",
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT ST_GeomFromText('POINT(1 1)')"}))
    assert out == {
        "success": False,
        "error_name": "FUNCTION_NOT_FOUND",
        "error_type": "USER_ERROR",
        "error_message": "line 1:8: Function 'st_geomfromtext' not registered",
        "error_location": {"line": 1, "column": 8},
        "query_id": "q1",
    }


def test_execute_tool_reports_exceptions_as_connection_error():
    with patch.object(tools, "execute_query_sync", side_effect=OSError("connection refused")):
        out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT 1"}))
    assert out["error_name"] == "CONNECTION_ERROR"
    assert out["error_message"] == "connection refused"


def test_validate_returns_trinos_error():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:32: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 32),
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT x FROM t"}))
    assert out["valid"] is False
    assert out["error_name"] == "COLUMN_NOT_FOUND"
    assert out["error_type"] == "USER_ERROR"
    assert "success" not in out



def test_validate_reports_location_against_the_callers_sql():
    # Trino sees "EXPLAIN (TYPE VALIDATE) SELECT x FROM t"; 'x' is at column 32 there, column 8 in the caller's SQL.
    res = TrinoExecutionResult(
        success=False, error_message="line 1:32: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 32),
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT x FROM t"}))
    assert out["error_location"] == {"line": 1, "column": 8}
    assert out["error_message"] == "line 1:32: Column 'x' cannot be resolved"  # Trino's message stays raw


def test_validate_leaves_later_lines_alone():
    res = TrinoExecutionResult(
        success=False, error_message="line 3:7: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(3, 7),
    )
    with patch.object(tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT a\nFROM t\nWHERE x = 1"}))
    assert out["error_location"] == {"line": 3, "column": 7}
