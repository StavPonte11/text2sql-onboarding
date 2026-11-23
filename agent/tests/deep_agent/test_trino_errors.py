import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import core.trino as core_trino
import pytest
import trino.exceptions
from core.trino import TrinoExecutionResult

from deep_agent import tools
from deep_agent.tools import sql as sql_tools
from deep_agent.tools import trino_errors

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
    assert res.trino_message == error["message"]  # Trino's own message, for the deep agent
    assert res.error_message.startswith("TrinoUserError(") and error["errorName"] in res.error_message  # unchanged for legacy
    assert res.query_id == "20261020_000000_00001_abcde"

    out = trino_errors.from_result(res)
    assert out["name"] == error["errorName"]
    assert out["type"] == error["errorType"]
    assert out["message"] == error["message"]
    location = error.get("errorLocation")
    if location:
        assert out["location"] == {"line": location["lineNumber"], "column": location["columnNumber"]}
    else:
        assert "location" not in out


def test_connection_failure_is_reported_as_connection_error():
    exc = ConnectionError("HTTPConnectionPool(host='localhost', port=8080): Max retries exceeded with url: /v1/statement")
    with patch.object(core_trino.settings, "TRINO_ENABLED", True), \
         patch.object(core_trino, "get_trino_connection", return_value=_failing_connection(exc)):
        res = core_trino.execute_query_sync("SELECT 1")
    out = trino_errors.from_result(res)
    assert out["name"] == "CONNECTION_ERROR"
    assert "type" not in out
    assert "Max retries exceeded" in out["message"]


def test_execute_tool_returns_trinos_error():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:8: Function 'st_geomfromtext' not registered",
        error_name="FUNCTION_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 8), query_id="q1",
    )
    with patch.object(sql_tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT ST_GeomFromText('POINT(1 1)')"}))
    assert out == {
        "ok": False,
        "data": None,
        "error": {
            "name": "FUNCTION_NOT_FOUND",
            "message": "line 1:8: Function 'st_geomfromtext' not registered",
            "type": "USER_ERROR",
            "location": {"line": 1, "column": 8},
            "query_id": "q1",
            "execution_time_ms": 0,
        },
    }


def test_execute_tool_reports_exceptions_as_connection_error():
    with patch.object(sql_tools, "execute_query_sync", side_effect=OSError("connection refused")):
        out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT 1"}))
    assert out["error"]["name"] == "CONNECTION_ERROR"
    assert out["error"]["message"] == "connection refused"


def test_validate_returns_trinos_error():
    res = TrinoExecutionResult(
        success=False, error_message="line 1:32: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 32),
    )
    with patch.object(sql_tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT x FROM t"}))
    assert out["ok"] is False
    assert out["error"]["name"] == "COLUMN_NOT_FOUND"
    assert out["error"]["type"] == "USER_ERROR"


def test_validate_reports_location_against_the_callers_sql():
    # Trino sees "EXPLAIN (TYPE VALIDATE) SELECT x FROM t"; 'x' is at column 32 there, column 8 in the caller's SQL.
    res = TrinoExecutionResult(
        success=False, error_message="line 1:32: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(1, 32),
    )
    with patch.object(sql_tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT x FROM t"}))
    assert out["error"]["location"] == {"line": 1, "column": 8}
    assert out["error"]["message"] == "line 1:32: Column 'x' cannot be resolved"  # Trino's message stays raw


def test_validate_leaves_later_lines_alone():
    res = TrinoExecutionResult(
        success=False, error_message="line 3:7: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR", error_location=(3, 7),
    )
    with patch.object(sql_tools, "execute_query_sync", return_value=res):
        out = json.loads(tools.validate_sql.invoke({"sql": "SELECT a\nFROM t\nWHERE x = 1"}))
    assert out["error"]["location"] == {"line": 3, "column": 7}
