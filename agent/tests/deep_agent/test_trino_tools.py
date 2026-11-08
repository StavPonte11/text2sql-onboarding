import json
from unittest.mock import patch

import pytest
from core.trino import TrinoExecutionResult

from deep_agent import tools
from deep_agent.tools import sql as sql_tools


def _ok(n_rows: int) -> TrinoExecutionResult:
    return TrinoExecutionResult(
        success=True, rows=[[i] for i in range(n_rows)], columns=["id"], row_count=n_rows, execution_time_ms=1
    )


@pytest.fixture
def trino():
    with patch.object(sql_tools, "execute_query_sync") as mock:
        yield mock


@pytest.mark.parametrize("sql", ["DROP TABLE t", "DELETE FROM t", "SELECT 1; DROP TABLE t", "SELECT 1 -- x\n; DROP TABLE t"])
def test_forbidden_sql_never_reaches_trino(trino, sql):
    out = json.loads(tools.execute_trino_query.invoke({"sql": sql}))
    assert out["ok"] is False
    assert out["error"]["name"] == "FORBIDDEN_STATEMENT"
    trino.assert_not_called()


def test_limit_argument_is_clamped_and_truncation_reported(trino):
    trino.return_value = _ok(101)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT id FROM t", "limit": 1_000_000}))
    executed = trino.call_args.args[0]
    assert executed.endswith("LIMIT 101")
    assert out["data"]["row_count"] == 100
    assert out["data"]["truncated"] is True


def test_omitted_limit_uses_the_configured_ceiling(trino, monkeypatch):
    monkeypatch.setattr(sql_tools.settings, "DEEP_AGENT_MAX_ROWS", 100)
    trino.return_value = _ok(5)
    tools.execute_trino_query.invoke({"sql": "SELECT id FROM t"})
    assert trino.call_args.args[0] == "SELECT id FROM t LIMIT 101"


def test_no_ceiling_runs_sql_as_written_and_returns_every_row(trino, monkeypatch):
    monkeypatch.setattr(sql_tools.settings, "DEEP_AGENT_MAX_ROWS", None)
    trino.return_value = _ok(5000)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT id FROM t;"}))
    assert trino.call_args.args[0] == "SELECT id FROM t"
    assert out["data"]["row_count"] == 5000
    assert out["data"]["truncated"] is False


def test_no_ceiling_still_honours_an_explicit_limit(trino, monkeypatch):
    monkeypatch.setattr(sql_tools.settings, "DEEP_AGENT_MAX_ROWS", None)
    trino.return_value = _ok(11)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT id FROM t", "limit": 10}))
    assert trino.call_args.args[0] == "SELECT id FROM t LIMIT 11"
    assert out["data"]["row_count"] == 10
    assert out["data"]["truncated"] is True


@pytest.mark.parametrize("raw", ["none", "None", "", "unlimited"])
def test_row_ceiling_can_be_disabled_from_env(monkeypatch, raw):
    from agent.config import AgentSettings

    monkeypatch.setenv("DEEP_AGENT_MAX_ROWS", raw)
    monkeypatch.setenv("DEEP_AGENT_MAX_SAMPLE_ROWS", raw)
    s = AgentSettings()
    assert s.DEEP_AGENT_MAX_ROWS is None
    assert s.DEEP_AGENT_MAX_SAMPLE_ROWS is None


def test_row_ceiling_defaults(monkeypatch):
    from agent.config import AgentSettings

    monkeypatch.delenv("DEEP_AGENT_MAX_ROWS", raising=False)
    monkeypatch.delenv("DEEP_AGENT_MAX_SAMPLE_ROWS", raising=False)
    s = AgentSettings(_env_file=None)
    assert (s.DEEP_AGENT_MAX_ROWS, s.DEEP_AGENT_MAX_SAMPLE_ROWS) == (100, 10)


def test_speed_limit_column_still_gets_limit(trino):
    trino.return_value = _ok(3)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "```sql\nSELECT speed_limit FROM t;\n```", "limit": 10}))
    assert trino.call_args.args[0] == "SELECT speed_limit FROM t LIMIT 11"
    assert out["data"]["truncated"] is False
    assert out["data"]["row_count"] == 3


def test_trino_error_is_reported(trino):
    trino.return_value = TrinoExecutionResult(
        success=False, error_message="line 1:8: Column 'x' cannot be resolved",
        error_name="COLUMN_NOT_FOUND", error_type="USER_ERROR",
    )
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT x FROM t"}))
    assert out["error"]["name"] == "COLUMN_NOT_FOUND"


def test_sample_rows_handles_hebrew_quoted_table(trino):
    trino.return_value = _ok(2)
    tools.get_sample_rows.invoke({"table_name": '"postgres"."public"."טבלת ערים"', "limit": 50})
    assert trino.call_args.args[0] == 'SELECT * FROM "postgres"."public"."טבלת ערים" LIMIT 11'


def test_sample_rows_rejects_injection(trino):
    out = json.loads(tools.get_sample_rows.invoke({"table_name": "t; DROP TABLE t"}))
    assert out["error"]["name"] == "FORBIDDEN_STATEMENT"
    trino.assert_not_called()


def test_distinct_values_quotes_hebrew_column(trino):
    trino.return_value = _ok(1)
    tools.get_column_distinct_values.invoke({"table_name": "postgres.public.t", "column_name": "שם עיר", "limit": 20})
    assert trino.call_args.args[0] == 'SELECT DISTINCT "שם עיר" FROM postgres.public.t LIMIT 21'


def test_validate_rejects_writes_without_calling_trino(trino):
    out = json.loads(tools.validate_sql.invoke({"sql": "DELETE FROM t"}))
    assert out["ok"] is False
    assert out["error"]["name"] == "FORBIDDEN_STATEMENT"
    trino.assert_not_called()


def test_validate_runs_explain_for_reads(trino):
    trino.return_value = _ok(0)
    assert json.loads(tools.validate_sql.invoke({"sql": "SELECT 1"}))["data"] == {"valid": True}
    assert trino.call_args.args[0] == "EXPLAIN (TYPE VALIDATE) SELECT 1"


def test_sample_rows_says_when_the_limit_was_reduced(monkeypatch):
    monkeypatch.setattr(sql_tools.settings, "DEEP_AGENT_MAX_SAMPLE_ROWS", 10)
    res = TrinoExecutionResult(success=True, rows=[[i] for i in range(11)], columns=["a"], row_count=11, query_id="q")
    with patch.object(sql_tools, "execute_query_sync", return_value=res):
        reduced = json.loads(tools.get_sample_rows.invoke({"table_name": "t", "limit": 200}))
        normal = json.loads(tools.get_sample_rows.invoke({"table_name": "t", "limit": 5}))
    assert "limit reduced from 200 to 10" in reduced["data"]["notice"] and reduced["data"]["row_count"] == 10
    assert "notice" not in normal["data"]
