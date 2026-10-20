"""
SQL guardrails for LLM-generated Trino SQL.

Parses with sqlglot, allows only a single read statement, enforces a row cap at the
AST level, and builds identifiers safely. Ported from Utils-Server
(app/services/trino/guard.py) so both agents share one implementation.
"""

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Tokenizer

_DIALECT = "trino"
_SHOW = re.compile(
    r"^\s*SHOW\s+(CATALOGS|SCHEMAS|TABLES|COLUMNS|FUNCTIONS|STATS|CREATE\s+(TABLE|VIEW)|SESSION)\b",
    re.IGNORECASE,
)
_READ_ROOTS = (exp.Select, exp.SetOperation, exp.Subquery)


class GuardError(ValueError):
    """The SQL was rejected; the message is safe to show the caller (and the agent)."""


@dataclass(frozen=True)
class GuardedSQL:
    sql: str                    # rewritten SQL to execute
    row_cap: int | None         # rows to keep (fetch row_cap + 1 to detect truncation); None for SHOW/DESCRIBE
    requested_limit: int | None  # LIMIT the SQL asked for, if any
    is_query: bool              # SELECT-like (row cap applied) vs SHOW/DESCRIBE


def clamp(value: int | None, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    return max(low, min(int(value), high))


def _has_comments(sql: str) -> bool:
    try:
        return any(token.comments for token in Tokenizer(dialect=_DIALECT).tokenize(sql))
    except SqlglotError:
        return "--" in sql or "/*" in sql


def _reject_pass_through(tree: exp.Expression) -> None:
    # TABLE(catalog.system.query(query => '...')) runs arbitrary SQL on the underlying source.
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Anonymous) and str(table.this.this).upper() == "TABLE":
            raise GuardError("table functions (TABLE(...)) are not allowed")
    for func in tree.find_all(exp.Anonymous):
        if str(func.this).upper() == "TABLE":
            raise GuardError("table functions (TABLE(...)) are not allowed")


def _reject_writes(tree: exp.Expression) -> None:
    # Data-modifying nodes nested inside an otherwise read-looking statement (e.g. CTAS-like constructs).
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter)):
            raise GuardError(f"{type(node).__name__.upper()} is not allowed: read-only access")


def _limit_value(tree: exp.Expression) -> int | None:
    lim = tree.args.get("limit")
    if isinstance(lim, exp.Limit) and isinstance(lim.expression, exp.Literal) and not lim.expression.is_string:
        return int(lim.expression.this)
    return None


def parse_read_statement(sql: str) -> exp.Expression | None:
    """Validate `sql` as exactly one read-only statement.

    Returns the parsed tree for SELECT-like statements, or None for an allowed
    SHOW/DESCRIBE statement. Raises GuardError otherwise.
    """
    if not sql or not sql.strip():
        raise GuardError("empty SQL")
    if _has_comments(sql):
        raise GuardError("SQL comments are not allowed")
    try:
        statements = [s for s in sqlglot.parse(sql, read=_DIALECT) if s is not None]
    except SqlglotError as e:
        raise GuardError(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise GuardError("exactly one statement is allowed")
    tree = statements[0]

    if isinstance(tree, exp.Command):
        if _SHOW.match(sql):
            return None
        raise GuardError("only SELECT, WITH, SHOW and DESCRIBE statements are allowed")
    if isinstance(tree, exp.Describe):
        return None
    if not isinstance(tree, _READ_ROOTS):
        raise GuardError(f"{type(tree).__name__.upper()} statements are not allowed: read-only access")

    _reject_pass_through(tree)
    _reject_writes(tree)
    return tree


def prepare(sql: str, row_cap: int) -> GuardedSQL:
    """Validate `sql` and enforce an outer LIMIT of row_cap + 1 (the extra row flags truncation)."""
    tree = parse_read_statement(sql)
    if tree is None:
        return GuardedSQL(sql.strip().rstrip(";"), None, None, False)

    fetch = row_cap + 1
    asked = _limit_value(tree)
    if isinstance(tree, exp.Select) and not tree.args.get("fetch"):
        if asked is None or asked > fetch:
            tree = tree.limit(fetch, copy=True)
    elif asked is None or asked > fetch or isinstance(tree, exp.SetOperation) or tree.args.get("fetch"):
        tree = exp.select("*").from_(tree.subquery("_q")).limit(fetch)
    return GuardedSQL(tree.sql(dialect=_DIALECT), row_cap, asked, True)


def parse_table_name(table_name: str) -> exp.Table:
    """Parse a (possibly quoted, 1–3 part) table name; reject anything that isn't a plain table."""
    raw = (table_name or "").strip()
    if not raw:
        raise GuardError("empty table name")
    try:
        table = sqlglot.parse_one(raw, into=exp.Table, dialect=_DIALECT)
    except SqlglotError as e:
        raise GuardError(f"invalid table name {raw!r}") from e
    parts = [table.args.get(k) for k in ("catalog", "db", "this")]
    if not isinstance(table.this, exp.Identifier) or any(
        p is not None and not isinstance(p, exp.Identifier) for p in parts
    ):
        raise GuardError(f"invalid table name {raw!r}")
    if set(table.args) - {"catalog", "db", "this"} and any(
        v for k, v in table.args.items() if k not in ("catalog", "db", "this")
    ):
        raise GuardError(f"invalid table name {raw!r}")
    return exp.Table(**{k: v for k, v in zip(("catalog", "db", "this"), parts) if v is not None})


def quoted_column(column_name: str) -> exp.Column:
    """Build a quoted column reference from a raw or double-quoted name (spaces/Hebrew allowed)."""
    name = (column_name or "").strip()
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('""', '"')
    if not name:
        raise GuardError("empty column name")
    return exp.column(exp.to_identifier(name, quoted=True))


def sample_rows_sql(table_name: str, row_cap: int) -> str:
    table = parse_table_name(table_name)
    return exp.select("*").from_(table).limit(row_cap + 1).sql(dialect=_DIALECT)


def distinct_values_sql(table_name: str, column_name: str, row_cap: int) -> str:
    table = parse_table_name(table_name)
    column = quoted_column(column_name)
    return exp.select(column).distinct().from_(table).limit(row_cap + 1).sql(dialect=_DIALECT)
