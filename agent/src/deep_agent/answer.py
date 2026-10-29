"""
Structured final answer for the Deep Agent, bound to SQL that actually ran.

The model finishes by calling the `submit_answer` tool (ToolStrategy structured output).
AnswerVerificationMiddleware then binds it to evidence. `final_result.executed` says whether
the final SQL ran and the rows shown are Trino's — it does NOT mean the answer was checked
to be semantically correct.

- "matched":      answer.sql equals (sqlglot-normalised) a query that executed successfully in
                  this run -> the result rows are that execution's rows
- "re_executed":  no match, or the match was only a smaller sample than the configured row
                  ceiling -> answer.sql is run once through the read-only guard; rows come from that run
- "rejected" / "re_execution_failed": the answer's SQL is forbidden or fails -> not executed
- "clarification": the model asked a clarifying question instead of answering
                  (only possible when DEEP_AGENT_CLARIFICATIONS_ENABLED is on)

The result table shown to users is always built from Trino rows, never from model text.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import sqlglot
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, hook_config
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, create_model
from sqlglot.errors import SqlglotError

from agent.config import settings
from agent.utils import sql_guard
from agent.utils.sql import clean_sql
from deep_agent.context import REMINDER_NAME, is_user_message
from core.trino import execute_query_sync

logger = logging.getLogger(__name__)

VerificationStatus = Literal["matched", "re_executed", "rejected", "re_execution_failed", "clarification", "no_answer"]


TOOL_NAME = "submit_answer"
# Content of the tool message for an accepted submission (anything else there is an error or a review's feedback).
ANSWER_RECEIVED = "Answer received."
_SUBMIT_DOC = "Submit your final answer. Call this exactly once, when you are done — it ends your work."


class _AnswerFields(BaseModel):
    sql: str = Field(
        description="The exact final Trino SQL whose results answer the question — the same query you executed "
                    "successfully with execute_trino_query.",
    )
    explanation_he: str = Field(description="Brief Hebrew explanation of what the SQL does and what the result means.")
    assumptions: list[str] = Field(
        default_factory=list, description="Interpretation choices you made (in Hebrew), e.g. which column means 'city'."
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence that the SQL answers the question.")


# The final-answer tool. LangChain's ToolStrategy names the tool after the schema's __name__, so the
# model sees a tool called `submit_answer` (the "I'm done" submit convention).
SubmitAnswer = create_model(TOOL_NAME, __base__=_AnswerFields, __doc__=_SUBMIT_DOC)

# Same tool name, plus the option to ask a clarifying question instead of answering.
SubmitAnswerWithClarification = create_model(
    TOOL_NAME,
    __base__=SubmitAnswer,
    __doc__=_SUBMIT_DOC,
    sql=(str | None, Field(
        default=None,
        description="The exact final Trino SQL whose results answer the question — the same query you executed "
                    "successfully with execute_trino_query. Null only when asking for clarification.",
    )),
    needs_clarification=(str | None, Field(
        default=None, description="If the question is ambiguous, the clarifying question to ask the user (Hebrew).",
    )),
)


_VALIDATION_RESPONSE = (str | None, Field(
    default=None,
    description="Only when an independent review rejected your previous answer and you submit the same SQL again: "
                "why that SQL is correct despite the review (Hebrew).",
))


def _with_validation_response(base: type[SubmitAnswer]) -> type[SubmitAnswer]:
    return create_model(TOOL_NAME, __base__=base, __doc__=_SUBMIT_DOC, validation_response=_VALIDATION_RESPONSE)


_SCHEMAS: dict[tuple[bool, bool], type[SubmitAnswer]] = {
    (False, False): SubmitAnswer,
    (True, False): SubmitAnswerWithClarification,
    (False, True): _with_validation_response(SubmitAnswer),
    (True, True): _with_validation_response(SubmitAnswerWithClarification),
}


def answer_schema(
    clarifications_enabled: bool | None = None, validation_enabled: bool | None = None
) -> type[SubmitAnswer]:
    """The final-answer schema; without clarifications the model must always commit to a SQL answer.

    With semantic validation "always", it also has `validation_response` (to stand by a rejected answer).
    """
    if clarifications_enabled is None:
        clarifications_enabled = settings.DEEP_AGENT_CLARIFICATIONS_ENABLED
    if validation_enabled is None:
        validation_enabled = settings.DEEP_AGENT_SEMANTIC_VALIDATION == "always"
    return _SCHEMAS[(bool(clarifications_enabled), bool(validation_enabled))]


def submission_strategy(schema: type[BaseModel]) -> ToolStrategy:
    """ToolStrategy whose accepted-submission tool message is ANSWER_RECEIVED (see accepted_submission)."""
    return ToolStrategy(schema, tool_message_content=ANSWER_RECEIVED)


def last_submission(messages: list, tool_name: str = TOOL_NAME) -> ToolMessage | None:
    """The tool message answering a submit call, when it is the latest message (i.e. the model just submitted)."""
    last = messages[-1] if messages else None
    return last if isinstance(last, ToolMessage) and last.name == tool_name else None


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
    if record is not None and not _is_sample(record):
        return {**base, "status": "matched", "executed": True, "sql": record["submitted_sql"], "result": _result_view(record)}

    status, record, error = _re_execute(answer.sql)
    if record is None:
        return {**base, "status": status, "executed": False, "sql": clean_sql(answer.sql), "result": None, "error": error}
    return {**base, "status": status, "executed": True, "sql": record["submitted_sql"], "result": _result_view(record)}


RECOVERED_FLAG = "deep_agent_recovered_answer"
_FENCED_JSON = re.compile(r"^```(?:json)?\s*(\{.*\})\s*```$", re.DOTALL)


def _answer_from_text(text: str, schema: type[BaseModel]) -> tuple[dict, BaseModel] | None:
    """The message text as a valid final answer (a JSON object that passes the answer schema), else None."""
    body = text.strip()
    fenced = _FENCED_JSON.match(body)
    if fenced:
        body = fenced.group(1)
    if not (body.startswith("{") and body.endswith("}")):
        return None
    try:
        args = json.loads(body)
        return args, schema(**args)
    except (ValueError, TypeError):
        return None


class AnswerRecoveryMiddleware(AgentMiddleware):
    """Recover a final answer the model wrote as text instead of calling submit_answer.

    With gpt-oss behind the configured OpenAI-compatible server, forced tool choice is not always
    honoured late in a run: the model sometimes writes the submit_answer JSON as plain text
    (finish_reason "tool_calls", no parsed tool call). LangChain then sees no tool call and the run
    ends without an answer. When — and only when — the text is a JSON object that validates against
    the answer schema, this turns it into the same result a real submit_answer call produces (the
    tool call, its tool message and `structured_response`), marked with RECOVERED_FLAG.
    Anything else is left untouched.
    """

    def _recover(self, request: ModelRequest, response: ModelResponse) -> ModelResponse:
        schema = getattr(request.response_format, "schema", None)
        if response.structured_response is not None or schema is None or not response.result:
            return response
        output = response.result[-1]
        if not isinstance(output, AIMessage) or output.tool_calls:
            return response
        parsed = _answer_from_text(output.text, schema)
        if parsed is None:
            return response
        args, answer = parsed
        call_id = f"recovered_{uuid.uuid4().hex[:12]}"
        logger.warning("Recovered a final answer the model wrote as text instead of a submit_answer call")
        repaired = AIMessage(
            content="",
            tool_calls=[{"name": schema.__name__, "args": args, "id": call_id, "type": "tool_call"}],
            additional_kwargs={**output.additional_kwargs, RECOVERED_FLAG: True},
            response_metadata=output.response_metadata,
            usage_metadata=output.usage_metadata,
            id=output.id,
            name=output.name,
        )
        tool_message = ToolMessage(content=ANSWER_RECEIVED, tool_call_id=call_id, name=schema.__name__)
        return ModelResponse(result=[*response.result[:-1], repaired, tool_message], structured_response=answer)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return self._recover(request, handler(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return self._recover(request, await handler(request))


def current_turn(messages: list) -> list:
    """Messages after the latest user message (this run's turn)."""
    for i in range(len(messages) - 1, -1, -1):
        if is_user_message(messages[i]):
            return messages[i + 1:]
    return messages


class SubmitReminderMiddleware(AgentMiddleware):
    """Send the model back once per turn when it stops without calling its submit tool.

    The configured model server sometimes ends a run with plain text instead of the forced
    submit call (and AnswerRecoveryMiddleware can only rescue text that is valid JSON for the
    schema). The work is then lost — e.g. a reviewer that found a real problem but never
    returned its verdict. One reminder (a message named REMINDER_NAME, not counted as the
    user's words) asks for the submit call; if the model still doesn't comply, the run ends as before.
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__()
        self.tool_name = tool_name

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls or state.get("structured_response") is not None:
            return None
        if any(getattr(m, "name", None) == REMINDER_NAME for m in current_turn(messages)):
            return None  # already reminded this turn
        reminder = (f"You stopped without calling `{self.tool_name}`. Your work is delivered only through "
                    f"`{self.tool_name}` — call it now with your result.")
        return {"messages": [HumanMessage(reminder, name=REMINDER_NAME)], "jump_to": "model"}

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


class AnswerVerificationMiddleware(AgentMiddleware):
    """Bind each turn's structured answer to SQL executed in that turn and its real rows.

    before_agent clears the previous turn's structured answer (it would otherwise be reported
    again on a turn that ends without a new one) and marks where this turn's executions start,
    so an answer can only match SQL that ran in its own turn.

    after_model sends the model back after a rejected submit_answer call (arguments that fail the
    schema). LangChain would retry on its own only while `structured_response` has never been set
    in the thread; from the first reset on, it ends the run instead.

    after_agent keeps the answer only as plain data in final_result and drops the pydantic object:
    checkpoints can't restore the dynamically built `submit_answer` class.
    """

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        submission = last_submission(state.get("messages") or [])
        if submission is not None and submission.text != ANSWER_RECEIVED:
            return {"jump_to": "model"}
        return None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"structured_response": None, "turn_execution_start": len(state.get("executed_queries") or [])}

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        executed = (state.get("executed_queries") or [])[state.get("turn_execution_start") or 0:]
        final = verify_answer(state.get("structured_response"), executed)
        final["recovered_from_text"] = any(
            (getattr(m, "additional_kwargs", None) or {}).get(RECOVERED_FLAG) for m in current_turn(state.get("messages") or [])
        )
        final["semantic_validation"] = validation_summary(state.get("semantic_validations"), final.get("sql"))
        return {"final_result": final, "structured_response": None}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.after_agent, state, runtime)


_VERDICTS = {"pass": "passed", "fail": "failed", "disputed": "disputed"}


def _covers(record: dict[str, Any], final_sql: str | None) -> bool | None:
    """Did this review look at the final SQL? None when it can't be told (an on-demand brief without a ```sql block)."""
    target = normalize_sql(final_sql)
    if "sql" in record:
        return record["sql"] == target
    return (target in record["sqls"]) if record.get("sqls") else None


def validation_summary(records: list[dict[str, Any]] | None, final_sql: str | None = None) -> dict[str, Any] | None:
    """The review outcome of the final answer, from this turn's review records (None = reviewer off).

    verdict: passed | failed | disputed (the agent resubmitted a rejected SQL with a justification) |
    not_validated (no review, a review of a different SQL than the final one, round limit, or the review failed).
    reviewed_final_sql: whether the last review covered the final SQL (None = can't be told).
    """
    if records is None:
        return None
    rounds = sum(1 for r in records if r["verdict"] in ("pass", "fail"))
    summary = {"verdict": "not_validated", "rounds": rounds, "issues": [], "response": None, "reason": None,
               "reviewed_final_sql": None}
    if not records:
        return {**summary, "reason": "no review"}
    last = records[-1]
    covers = _covers(last, final_sql)
    summary.update(issues=last.get("issues") or [], response=last.get("response"), reviewed_final_sql=covers,
                   reason=last.get("reason"))
    if covers is False:
        return {**summary, "issues": [], "reason": "the final SQL changed after the last review"}
    return {**summary, "verdict": _VERDICTS.get(last["verdict"], "not_validated")}


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
    elif final_result.get("status") == "no_answer":
        reason = final_result.get("error")
        parts.append("⚠️ לא התקבלה תשובה סופית" + (f": {reason}" if reason else "."))
    elif final_result.get("error"):
        parts.append(f"⚠️ לא ניתן היה להריץ את השאילתה הסופית: {final_result['error']}")

    review = final_result.get("semantic_validation") or {}
    if review.get("verdict") in ("failed", "disputed") and review.get("issues"):
        title = "בדיקה עצמאית מצאה בעיות בתשובה" if review["verdict"] == "failed" else "בדיקה עצמאית העלתה הסתייגות"
        parts.append(f"⚠️ **{title}:**\n" + "\n".join(f"- {i['problem']}" for i in review["issues"]))
        if review["verdict"] == "disputed" and review.get("response"):
            parts.append(f"**תגובת הסוכן:** {review['response']}")

    if final_result.get("sql"):
        parts.append(f"```sql\n{final_result['sql']}\n```")
    return "\n\n".join(p for p in parts if p)
