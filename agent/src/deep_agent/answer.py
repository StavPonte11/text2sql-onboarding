"""
Structured final answer for the Deep Agent, bound to SQL that actually ran.

The model finishes by submitting a DeepAgentAnswer (ToolStrategy structured output).
AnswerVerificationMiddleware then binds it to evidence:

- "matched":      answer.sql equals (sqlglot-normalised) a query that executed successfully in
                  this run -> the result rows are that execution's rows
- "re_executed":  no match -> answer.sql is run once through the read-only guard; rows come
                  from that execution
- "rejected" / "re_execution_failed": the answer's SQL is forbidden or fails -> not verified
- "clarification": the model asked a clarifying question instead of answering
- "best_effort":  no structured answer (call limit hit, timeout) but SQL did run -> the last
                  successful execution, with confidence=low

The result table shown to users is always built from Trino rows, never from model text.
"""

import asyncio
import logging
from typing import Any, Literal

import sqlglot
from langchain.agents.middleware import AgentMiddleware
from pydantic import BaseModel, Field
from sqlglot.errors import SqlglotError

from agent.config import settings
from agent.utils import sql_guard
from agent.utils.sql import clean_sql
from core.trino import execute_query_sync

logger = logging.getLogger(__name__)

VerificationStatus = Literal[
    "matched", "re_executed", "rejected", "re_execution_failed", "clarification", "best_effort", "no_answer"
]

BEST_EFFORT_EXPLANATION = (
    "לא התקבלה תשובה סופית ({reason}). מוצגת תוצאת השאילתה האחרונה שהורצה בהצלחה — יש לאמת אותה."
)


class DeepAgentAnswer(BaseModel):
    """Submit the final answer. Call this exactly once, when you are done."""

    sql: str | None = Field(
        default=None,
        description="The exact final Trino SQL whose results answer the question — the same query you executed "
                    "successfully with execute_trino_query. Null only when asking for clarification.",
    )
    explanation_he: str = Field(description="Brief Hebrew explanation of what the SQL does and what the result means.")
    assumptions: list[str] = Field(
        default_factory=list, description="Interpretation choices you made (in Hebrew), e.g. which column means 'city'."
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence that the SQL answers the question.")
    needs_clarification: str | None = Field(
        default=None, description="If the question is ambiguous, the clarifying question to ask the user (Hebrew)."
    )


def normalize_sql(sql: str | None) -> str | None:
    """Canonical form for comparing two SQL strings (whitespace, keyword case, trailing ';', fences)."""
    if not sql:
        return None
    cleaned = clean_sql(sql)
    try:
        return sqlglot.parse_one(cleaned, read="trino").sql(dialect="trino")
    except SqlglotError:
        return " ".join(cleaned.split())


def executed_query_record(submitted_sql: str, executed_sql: str, payload: dict[str, Any]) -> dict[str, Any]:
    """State entry for one successful execution (see DeepAgentRuntimeState.executed_queries)."""
    return {
        "submitted_sql": clean_sql(submitted_sql),
        "executed_sql": executed_sql,
        "columns": payload["columns"],
        "rows": payload["rows"],
        "row_count": payload["row_count"],
        "truncated": payload["truncated"],
        "query_id": payload.get("query_id"),
    }


def _match(answer_sql: str, executed: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = normalize_sql(answer_sql)
    for record in reversed(executed):  # latest execution wins
        if target in (normalize_sql(record["submitted_sql"]), normalize_sql(record["executed_sql"])):
            return record
    return None


def _resolve_geometries(sql: str, registry: dict[str, Any] | None) -> str:
    """SQL with location-analyst geometry placeholders substituted (unchanged when there are none)."""
    from deep_agent.geo.tools import substitute_geometries

    return substitute_geometries(sql, registry or {})


def _re_execute(answer_sql: str, registry: dict[str, Any] | None = None) -> tuple[VerificationStatus, dict[str, Any] | None, str | None]:
    row_cap = settings.DEEP_AGENT_MAX_ROWS
    try:
        resolved = _resolve_geometries(clean_sql(answer_sql), registry)
    except ValueError as e:
        return "rejected", None, str(e)
    try:
        guarded = sql_guard.prepare(resolved, row_cap)
    except sql_guard.GuardError as e:
        return "rejected", None, str(e)
    res = execute_query_sync(guarded.sql, reuse_connection=True)
    if not res.success:
        return "re_execution_failed", None, res.error_message
    rows = res.rows or []
    payload = {"columns": res.columns, "rows": rows[:row_cap], "row_count": min(len(rows), row_cap),
               "truncated": len(rows) > row_cap, "query_id": res.query_id}
    return "re_executed", executed_query_record(answer_sql, guarded.sql, payload), None


def _result_view(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record[k] for k in ("columns", "rows", "row_count", "truncated", "query_id")}


def best_effort_result(executed: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """Answer from the last successful execution when the agent stopped without a structured answer."""
    if not executed:
        return {"status": "no_answer", "verified": False, "answer": None, "sql": None, "result": None,
                "error": reason}
    record = executed[-1]
    answer = DeepAgentAnswer(
        sql=record["submitted_sql"],
        explanation_he=BEST_EFFORT_EXPLANATION.format(reason=reason),
        confidence="low",
    )
    return {"status": "best_effort", "verified": True, "answer": answer.model_dump(), "sql": record["submitted_sql"],
            "result": _result_view(record), "error": reason}


def verify_answer(
    answer: Any, executed: list[dict[str, Any]], stop_reason: str = "no structured answer",
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build `final_result` from the structured answer and the run's successful executions.

    `registry` is the thread's geo_registry: geometry placeholders in the SQL are substituted for
    re-execution and in the SQL shown to the user (data.sql stays runnable outside the agent).
    """
    return _display_sql(_verify(answer, executed, stop_reason, registry), registry)


def _display_sql(result: dict[str, Any], registry: dict[str, Any] | None) -> dict[str, Any]:
    if registry and result.get("sql"):
        try:
            result["sql"] = _resolve_geometries(result["sql"], registry)
        except ValueError:
            pass  # unknown placeholder: leave the SQL as submitted
    return result


def _verify(answer: Any, executed: list[dict[str, Any]], stop_reason: str, registry: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(answer, DeepAgentAnswer):
        return best_effort_result(executed, stop_reason)

    base = {"answer": answer.model_dump(), "error": None}
    if answer.needs_clarification or not answer.sql:
        return {**base, "status": "clarification", "verified": False, "sql": None, "result": None}

    record = _match(answer.sql, executed)
    if record is not None:
        return {**base, "status": "matched", "verified": True, "sql": record["submitted_sql"], "result": _result_view(record)}

    status, record, error = _re_execute(answer.sql, registry)
    if record is None:
        return {**base, "status": status, "verified": False, "sql": clean_sql(answer.sql), "result": None, "error": error}
    return {**base, "status": status, "verified": True, "sql": record["submitted_sql"], "result": _result_view(record)}


class AnswerVerificationMiddleware(AgentMiddleware):
    """Bind each turn's structured answer to executed SQL and real rows.

    before_agent clears the previous turn's structured answer (it would otherwise be reported
    again on a turn that ends without a new one) and marks where this turn's executions start.
    """

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"structured_response": None, "turn_execution_start": len(state.get("executed_queries") or [])}

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        executed = state.get("executed_queries") or []
        answer = state.get("structured_response")
        if not isinstance(answer, DeepAgentAnswer):
            # No answer this turn: fall back only to SQL that ran in this turn.
            return {"final_result": _display_sql(best_effort_result(
                executed[state.get("turn_execution_start") or 0:], "no structured answer"), state.get("geo_registry"))}
        return {"final_result": verify_answer(answer, executed, registry=state.get("geo_registry"))}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.after_agent, state, runtime)


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(final_result: dict[str, Any]) -> str:
    """User-facing Hebrew answer: explanation, table from real rows, SQL block."""
    answer = final_result.get("answer") or {}
    if final_result.get("status") == "clarification":
        return answer.get("needs_clarification") or answer.get("explanation_he", "")
    parts = [answer.get("explanation_he", "")]
    if answer.get("assumptions"):
        parts.append("**הנחות:**\n" + "\n".join(f"- {a}" for a in answer["assumptions"]))

    result = final_result.get("result")
    if result:
        header = "| " + " | ".join(_cell(c) for c in result["columns"]) + " |"
        sep = "|" + "---|" * len(result["columns"])
        body = ["| " + " | ".join(_cell(v) for v in row) + " |" for row in result["rows"]]
        parts.append("\n".join([header, sep, *body]))
        if result.get("truncated"):
            parts.append(f"_מוצגות {result['row_count']} השורות הראשונות בלבד._")
    elif final_result.get("error"):
        parts.append(f"⚠️ לא ניתן היה להריץ את השאילתה הסופית: {final_result['error']}")

    if final_result.get("sql"):
        parts.append(f"```sql\n{final_result['sql']}\n```")
    return "\n\n".join(p for p in parts if p)
