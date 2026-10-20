import json
from unittest.mock import patch

import pytest

from core.trino import TrinoExecutionResult
from deep_agent import tools


def _ok(n_rows: int) -> TrinoExecutionResult:
    return TrinoExecutionResult(
        success=True, rows=[[i] for i in range(n_rows)], columns=["id"], row_count=n_rows, execution_time_ms=1
    )


@pytest.fixture
def trino():
    with patch.object(tools, "execute_query_sync") as mock:
        yield mock


@pytest.mark.parametrize("sql", ["DROP TABLE t", "DELETE FROM t", "SELECT 1; DROP TABLE t", "SELECT 1 -- x"])
def test_forbidden_sql_never_reaches_trino(trino, sql):
    out = json.loads(tools.execute_trino_query.invoke({"sql": sql}))
    assert out["success"] is False
    assert out["error_category"] == "FORBIDDEN_STATEMENT"
    trino.assert_not_called()


def test_limit_argument_is_clamped_and_truncation_reported(trino):
    trino.return_value = _ok(101)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT id FROM t", "limit": 1_000_000}))
    executed = trino.call_args.args[0]
    assert executed.endswith("LIMIT 101")
    assert out["row_count"] == 100
    assert out["truncated"] is True


def test_speed_limit_column_still_gets_limit(trino):
    trino.return_value = _ok(3)
    out = json.loads(tools.execute_trino_query.invoke({"sql": "```sql\nSELECT speed_limit FROM t;\n```", "limit": 10}))
    assert trino.call_args.args[0] == "SELECT speed_limit FROM t LIMIT 11"
    assert out["truncated"] is False
    assert out["row_count"] == 3


def test_trino_error_is_reported(trino):
    trino.return_value = TrinoExecutionResult(success=False, error_message="line 1:8: Column 'x' cannot be resolved")
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT x FROM t"}))
    assert out["error_category"] == "COLUMN_NOT_FOUND"


def test_sample_rows_handles_hebrew_quoted_table(trino):
    trino.return_value = _ok(2)
    tools.get_sample_rows.invoke({"table_name": '"postgres"."public"."טבלת ערים"', "limit": 50})
    assert trino.call_args.args[0] == 'SELECT * FROM "postgres"."public"."טבלת ערים" LIMIT 11'


def test_sample_rows_rejects_injection(trino):
    out = json.loads(tools.get_sample_rows.invoke({"table_name": "t; DROP TABLE t"}))
    assert out["error_category"] == "FORBIDDEN_STATEMENT"
    trino.assert_not_called()


def test_distinct_values_quotes_hebrew_column(trino):
    trino.return_value = _ok(1)
    tools.get_column_distinct_values.invoke({"table_name": "postgres.public.t", "column_name": "שם עיר", "limit": 20})
    assert trino.call_args.args[0] == 'SELECT DISTINCT "שם עיר" FROM postgres.public.t LIMIT 21'


def test_validate_rejects_writes_without_calling_trino(trino):
    out = tools.validate_sql_syntax.invoke({"sql": "DELETE FROM t"})
    assert out.startswith("Syntax Error:")
    trino.assert_not_called()


def test_validate_runs_explain_for_reads(trino):
    trino.return_value = _ok(0)
    assert tools.validate_sql_syntax.invoke({"sql": "SELECT 1"}) == "Syntax is VALID."
    assert trino.call_args.args[0] == "EXPLAIN (TYPE VALIDATE) SELECT 1"
