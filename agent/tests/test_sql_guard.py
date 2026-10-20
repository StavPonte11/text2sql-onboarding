import pytest
import sqlglot
from sqlglot import exp

from agent.utils import sql_guard
from agent.utils.sql_guard import GuardError, clamp, distinct_values_sql, prepare, sample_rows_sql


def _outer_limit(sql: str) -> int | None:
    tree = sqlglot.parse_one(sql, read="trino")
    lim = tree.args.get("limit")
    return int(lim.expression.this) if lim else None


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
    "DROP TABLE t",
    "CREATE TABLE x AS SELECT * FROM t",
    "ALTER TABLE t ADD COLUMN c int",
    "CALL system.sync_partition_metadata('a', 'b', 'FULL')",
    "GRANT SELECT ON t TO u",
    "SET SESSION query_max_run_time = '1h'",
    "SELECT 1; DROP TABLE t",
    "SELECT 1; SELECT 2",
    "SELECT * FROM TABLE(postgres.system.query(query => 'DELETE FROM t'))",
    "SELECT 1 -- sneaky comment",
    "SELECT /* hidden */ 1",
    "",
    "   ",
])
def test_rejects_non_read_statements(sql):
    with pytest.raises(GuardError):
        prepare(sql, 50)


@pytest.mark.parametrize("sql", [
    "SELECT speed_limit FROM t",                                      # 'limit' substring bypass
    "WITH a AS (SELECT * FROM t LIMIT 5) SELECT * FROM a JOIN b ON 1=1",  # inner LIMIT only
    "SELECT * FROM t",
])
def test_unbounded_selects_get_an_outer_limit(sql):
    guarded = prepare(sql, 50)
    assert guarded.is_query
    assert _outer_limit(guarded.sql) == 51  # cap + 1 to detect truncation


def test_larger_explicit_limit_is_capped():
    guarded = prepare("SELECT * FROM t LIMIT 100000", 50)
    assert _outer_limit(guarded.sql) == 51
    assert guarded.requested_limit == 100000


def test_smaller_explicit_limit_is_kept():
    guarded = prepare("SELECT * FROM t LIMIT 5", 50)
    assert _outer_limit(guarded.sql) == 5


@pytest.mark.parametrize("sql", [
    "SELECT a FROM t UNION SELECT a FROM u",
    "SELECT a FROM t UNION ALL SELECT a FROM u LIMIT 1000",
    "SELECT a FROM t FETCH FIRST 1000 ROWS ONLY",
])
def test_set_operations_and_fetch_are_wrapped(sql):
    guarded = prepare(sql, 10)
    tree = sqlglot.parse_one(guarded.sql, read="trino")
    assert isinstance(tree, exp.Select)
    assert _outer_limit(guarded.sql) == 11


def test_string_literal_with_dashes_is_not_a_comment():
    guarded = prepare("SELECT * FROM t WHERE code = 'a--b'", 10)
    assert "'a--b'" in guarded.sql


@pytest.mark.parametrize("sql", ["SHOW TABLES FROM postgres.public", "DESCRIBE postgres.public.t"])
def test_show_and_describe_pass_without_limit(sql):
    guarded = prepare(sql, 10)
    assert not guarded.is_query
    assert "LIMIT" not in guarded.sql.upper()


def test_trailing_semicolon_is_accepted():
    assert prepare("SELECT 1;", 10).is_query


def test_clamp():
    assert clamp(1_000_000, 1, 100, 50) == 100
    assert clamp(0, 1, 100, 50) == 1
    assert clamp(None, 1, 100, 50) == 50


@pytest.mark.parametrize("table, expected", [
    ('"postgres"."public"."my table"', '"postgres"."public"."my table"'),
    ('"postgres"."public"."שם טבלה"', '"postgres"."public"."שם טבלה"'),
    ("postgres.public.flights_table", "postgres.public.flights_table"),
    ("flights_table", "flights_table"),
])
def test_sample_rows_sql_keeps_valid_identifiers(table, expected):
    sql = sample_rows_sql(table, 5)
    assert sql == f"SELECT * FROM {expected} LIMIT 6"


@pytest.mark.parametrize("table", [
    "x; DROP TABLE y",
    '"a"."b"."c" UNION SELECT * FROM secrets',
    "t WHERE 1=1",
    "(SELECT 1)",
    "",
])
def test_sample_rows_sql_rejects_injection(table):
    with pytest.raises(GuardError):
        sample_rows_sql(table, 5)


@pytest.mark.parametrize("column, expected", [
    ("launch_country", '"launch_country"'),
    ('"שם עיר"', '"שם עיר"'),
    ("שם עיר", '"שם עיר"'),
    ('weird"name', '"weird""name"'),
])
def test_distinct_values_sql_quotes_columns(column, expected):
    sql = distinct_values_sql('"postgres"."public"."t"', column, 20)
    assert sql == f'SELECT DISTINCT {expected} FROM "postgres"."public"."t" LIMIT 21'
    sql_guard.parse_read_statement(sql)  # the generated SQL itself passes the guard


def test_distinct_values_column_injection_is_neutralised():
    sql = distinct_values_sql("t", "a FROM t; DROP TABLE t", 5)
    # The whole input becomes one quoted identifier, never executable SQL.
    assert sql == 'SELECT DISTINCT "a FROM t; DROP TABLE t" FROM t LIMIT 6'
    sql_guard.parse_read_statement(sql)
