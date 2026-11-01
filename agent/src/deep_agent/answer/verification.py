"""
Binding the submitted answer to SQL that actually ran (final_result).

The model finishes by calling the `submit_answer` tool (ToolStrategy structured output).
AnswerVerificationMiddleware then binds it to evidence. `final_result.executed` says whether
the final SQL ran and the rows shown are Trino's — it does NOT mean the answer was checked
to be semantically correct.

- "matched":      answer.sql equals (sqlglot-normalised) a query that executed successfully in
                  this turn -> the result rows are that execution's rows
- "re_executed":  it matched only a sample (a smaller limit than the configured row ceiling) -> the same,
                  already approved SQL is run once more to show all its rows
- "re_execution_failed": that full-rows run failed -> not executed
- "not_executed": the SQL never ran in this turn (SubmitApprovalMiddleware refuses such a submission,
                  so this only marks a broken invariant)
- "clarification": the model asked a clarifying question instead of answering
                  (only possible when DEEP_AGENT_CLARIFICATIONS_ENABLED is on)

The result table shown to users is always built from Trino rows, never from model text.
"""

from typing import Any, Literal

import sqlglot
from sqlglot.errors import SqlglotError

from agent.config import settings
from agent.utils import sql_guard
from agent.utils.sql import clean_sql
from core.trino import execute_query_sync
from deep_agent.answer.schema import SubmitAnswer

VerificationStatus = Literal["matched", "re_executed", "rejected", "re_execution_failed", "not_executed", "clarification",
                             "no_answer"]


def normalize_sql(sql: str | None) -> str | None:
    """Canonical form for comparing two SQL strings (whitespace, keyword case, trailing ';', fences)."""
    if not sql:
        return None
    cleaned = clean_sql(sql)
    try:
        return sqlglot.parse_one(cleaned, read="trino").sql(dialect="trino")
    except SqlglotError:
        return " ".join(cleaned.split())


def executed_query_record(
    submitted_sql: str, executed_sql: str, payload: dict[str, Any], row_cap: int | None
) -> dict[str, Any]:
    """State entry for one successful execution (see DeepAgentRuntimeState.executed_queries).

    row_cap is the ceiling that execution ran under (None = none), so verification can tell an
    exploration sample (e.g. limit=5) from the full answer.
    """
    return {
        "submitted_sql": clean_sql(submitted_sql),
        "executed_sql": executed_sql,
        "columns": payload["columns"],
        "rows": payload["rows"],
        "row_count": payload["row_count"],
        "truncated": payload["truncated"],
        "query_id": payload.get("query_id"),
        "row_cap": row_cap,
    }


def match_execution(answer_sql: str, executed: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = normalize_sql(answer_sql)
    for record in reversed(executed):  # latest execution wins
        if target in (normalize_sql(record["submitted_sql"]), normalize_sql(record["executed_sql"])):
            return record
    return None


def _is_sample(record: dict[str, Any]) -> bool:
    """The matched execution was cut short by a smaller limit than the configured row ceiling."""
    if not record.get("truncated"):
        return False
    ceiling = settings.DEEP_AGENT_MAX_ROWS
    cap = record.get("row_cap")
    return cap is not None and (ceiling is None or cap < ceiling)


def _re_execute(answer_sql: str) -> tuple[VerificationStatus, dict[str, Any] | None, str | None]:
    row_cap = settings.DEEP_AGENT_MAX_ROWS
    try:
        guarded = sql_guard.prepare(clean_sql(answer_sql), row_cap)
    except sql_guard.GuardError as e:
        return "rejected", None, str(e)
    res = execute_query_sync(guarded.sql, reuse_connection=True)
    if not res.success:
        return "re_execution_failed", None, res.error_message
    rows = res.rows or []
    truncated = row_cap is not None and len(rows) > row_cap
    if truncated:
        rows = rows[:row_cap]
    payload = {"columns": res.columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
               "query_id": res.query_id}
    return "re_executed", executed_query_record(answer_sql, guarded.sql, payload, row_cap), None


def _result_view(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record[k] for k in ("columns", "rows", "row_count", "truncated", "query_id")}


def verify_answer(answer: Any, executed: list[dict[str, Any]]) -> dict[str, Any]:
    """Build `final_result` from the structured answer and the run's successful executions."""
    if not isinstance(answer, SubmitAnswer):
        return {"status": "no_answer", "executed": False, "answer": None, "sql": None, "result": None, "error": None}

    base = {"answer": answer.model_dump(), "error": None}
    if getattr(answer, "needs_clarification", None) or not answer.sql:
        return {**base, "status": "clarification", "executed": False, "sql": None, "result": None}

    record = match_execution(answer.sql, executed)
    if record is None:
        return {**base, "status": "not_executed", "executed": False, "sql": clean_sql(answer.sql), "result": None,
                "error": "the submitted SQL did not run in this turn"}
    if not _is_sample(record):
        return {**base, "status": "matched", "executed": True, "sql": record["submitted_sql"], "result": _result_view(record)}

    status, record, error = _re_execute(answer.sql)  # the approved SQL ran only as a sample: fetch all its rows
    if record is None:
        return {**base, "status": status, "executed": False, "sql": clean_sql(answer.sql), "result": None, "error": error}
    return {**base, "status": status, "executed": True, "sql": record["submitted_sql"], "result": _result_view(record)}


_VERDICTS = {"pass": "passed", "fail": "failed", "disputed": "disputed"}


def validation_summary(records: list[dict[str, Any]] | None, final_sql: str | None = None) -> dict[str, Any] | None:
    """The review outcome of the final answer, from this turn's review records (None = review off).

    verdict: the latest record for the final SQL — passed | disputed (the agent submitted a failed SQL with a
    justification) | failed | not_validated (no review of the final SQL, review limit, or the review errored).
    rounds: reviews this turn (any SQL).
    """
    if records is None:
        return None
    rounds = sum(1 for r in records if r["verdict"] in ("pass", "fail"))
    summary = {"verdict": "not_validated", "rounds": rounds, "issues": [], "response": None, "reason": None,
               "reviewed_final_sql": False}
    target = normalize_sql(final_sql)
    relevant = [r for r in records if target is not None and r.get("sql") == target]
    if not relevant:
        return {**summary, "reason": "no review of the final SQL"}
    last = relevant[-1]
    return {**summary, "verdict": _VERDICTS.get(last["verdict"], "not_validated"), "issues": last.get("issues") or [],
            "response": last.get("response"), "reason": last.get("reason"), "reviewed_final_sql": True}
