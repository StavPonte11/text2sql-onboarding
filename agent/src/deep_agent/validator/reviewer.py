"""
The reviewer agent (the `semantic-validator` subagent): a separate agent with a fresh context that
judges by reading — it has no SQL tools and doesn't write SQL. Its prompt is prompts/validator.md.

Verdicts (`submit_verdict`):
- pass        the SQL answers the request
- fail        a problem it can show from what it read (issues with evidence)
- needs_check rarely: something it can see gives a concrete reason to doubt that only data can settle;
              it names the specific checks, the main agent runs them and asks for review again

Independence: the agent that asks for the review writes a brief, but the reviewer never reads it as
such. ReviewInputMiddleware replaces the brief with an input our code builds from facts:

- the user's messages, from the thread (state `user_messages`) — never from the brief
- the SQL to review and the agent's assumptions — the only two things taken from the brief
  (a ```sql block and a "- " list); any other text in the brief is discarded
- that SQL's result rows, from the recorded execution (state `executed_queries`)
- after a needs_check: the checks it asked for, and the real results of the queries the agent ran since
  (from the recorded executions — never the agent's description of them)
- plus, like every model call: today's date and the connection's catalog

Not given: the agent's reasoning, notes, confidence, or the user-facing explanation.
ReviewRecordMiddleware records each verdict for the reviewed SQL in `semantic_validations`, which
submit_answer checks (deep_agent.answer.middleware).
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, create_model, model_validator

from deep_agent.answer.middleware import submit_fallback
from deep_agent.answer.schema import submission_strategy
from deep_agent.answer.verification import match_execution, normalize_sql
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware, DeepAgentRuntimeState
from deep_agent.middleware.catalog import CatalogMiddleware
from deep_agent.prompts.registry import PromptRefreshMiddleware, load_prompt

logger = logging.getLogger(__name__)

ValidationMode = Literal["always", "on_demand", "off"]

VERDICT_TOOL = "submit_verdict"
VALIDATOR_NAME = "semantic-validator"
REVIEW_CONFIG = {"run_name": VALIDATOR_NAME, "tags": [VALIDATOR_NAME]}  # the tag marks the reviewer's nested runs


class Issue(BaseModel):
    problem: str = Field(description="What is wrong and how to fix it (Hebrew).")
    evidence: str = Field(description="The exact part of the SQL, the result rows, the catalog or the request that "
                                      "shows it (Hebrew).")


class _VerdictFields(BaseModel):
    verdict: Literal["pass", "fail", "needs_check"] = Field(
        description="pass = the SQL answers the request; fail = it doesn't; needs_check = something you can see "
                    "gives a concrete reason to doubt that only data can settle (rare).")
    issues: list[Issue] = Field(default_factory=list, description="Every problem found (required for fail).")
    checks: list[str] = Field(
        default_factory=list,
        description="For needs_check: each specific check for the analyst to run, e.g. 'check whether the values of "
                    "table_a.col match table_b.col'.")

    @model_validator(mode="after")
    def _verdict_needs_its_details(self):
        if self.verdict == "fail" and not self.issues:
            raise ValueError("a fail verdict needs at least one issue with evidence")
        if self.verdict == "needs_check" and not self.checks:
            raise ValueError("a needs_check verdict needs at least one specific check")
        return self


SubmitVerdict = create_model(
    VERDICT_TOOL, __base__=_VerdictFields,
    __doc__="Submit your review verdict. Call this exactly once, when you are done — it ends your review.",
)

# A real code block: the ```sql fence starts a line and the SQL is on the lines below (an inline mention of
# "```sql block" in prose is not one).
_SQL_BLOCK = re.compile(r"^[ \t]*```sql[ \t]*\n(.*?)^[ \t]*```", re.DOTALL | re.IGNORECASE | re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")


def parse_brief(brief: str) -> tuple[str | None, list[str]]:
    """The two things taken from the agent's brief: the SQL (first ```sql block) and its assumptions (bullets)."""
    blocks = _SQL_BLOCK.findall(brief or "")
    sql = blocks[0].strip() if blocks else None
    outside = _SQL_BLOCK.sub("", brief or "")
    assumptions = [m.group(1) for line in outside.splitlines() if (m := _BULLET.match(line))]
    return sql or None, assumptions


def _rows_section(record: dict[str, Any]) -> str:
    note = " (only part of the result: more rows exist)" if record.get("truncated") else ""
    lines = [f"Columns: {record['columns']}", f"Rows returned: {record['row_count']}{note}"]
    lines += [str(row) for row in record["rows"]]
    return "\n".join(lines)


def _pending_checks(records: list[dict[str, Any]], executions: list[dict[str, Any]]) -> tuple[list[str], list[dict]]:
    """The checks asked for in the latest needs_check review this turn, and the executions recorded since it."""
    asked = [r for r in records if r.get("verdict") == "needs_check"]
    if not asked:
        return [], []
    latest = asked[-1]
    return latest.get("checks") or [], executions[latest.get("after_execution", len(executions)):]


def review_request(user_messages: list[str], sql: str | None, assumptions: list[str],
                   executed: list[dict[str, Any]], records: list[dict[str, Any]] | None = None) -> str:
    """The reviewer's input, built from facts: the user's own messages, the SQL, its assumptions, its real rows,
    and — after a needs_check — the checks asked for and the real results of the queries run since."""
    parts = []
    if user_messages[:-1]:
        parts.append("## Earlier user messages (context)\n" + "\n".join(f"- {text}" for text in user_messages[:-1]))
    parts.append(f"## Current user request\n{user_messages[-1] if user_messages else ''}")
    if sql is None:
        parts.append("## SQL to review\n(none was provided — fail the review with the issue "
                     "\"no SQL was sent for review\")")
        return "\n\n".join(parts)
    parts.append(f"## SQL to review\n```sql\n{sql}\n```")
    listed = "\n".join(f"- {a}" for a in assumptions) or "(none stated)"
    parts.append("## The analyst's interpretation choices — claims to check, not facts\n" + listed)
    record = match_execution(sql, executed)
    parts.append("## Result of this SQL\n" + (_rows_section(record) if record else "This SQL has not run in this turn."))
    checks, since = _pending_checks(records or [], executed)
    if checks:
        parts.append("## Checks you asked for in your previous review\n" + "\n".join(f"- {c}" for c in checks))
        if since:
            ran = [f"```sql\n{r['submitted_sql']}\n```\n{_rows_section(r)}" for r in since]
            parts.append("## Queries the analyst ran since, with their real results\n" + "\n\n".join(ran))
        else:
            parts.append("## Queries the analyst ran since\n(none)")
    return "\n\n".join(parts)


def _turn_executions(state: dict) -> list[dict[str, Any]]:
    return (state.get("executed_queries") or [])[state.get("turn_execution_start") or 0:]


class ReviewInputMiddleware(AgentMiddleware):
    """Inside the reviewer: replace the agent's brief with the input built from facts (module docstring)."""

    state_schema = DeepAgentRuntimeState

    def _with_review_input(self, request: ModelRequest) -> ModelRequest:
        messages = list(request.messages)
        if not messages or not isinstance(messages[0], HumanMessage):
            return request
        state = request.state or {}
        sql, assumptions = parse_brief(messages[0].text)
        text = review_request(list(state.get("user_messages") or []), sql, assumptions, _turn_executions(state),
                              list(state.get("semantic_validations") or []))
        messages[0] = HumanMessage(text, id=messages[0].id)
        return request.override(messages=messages)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self._with_review_input(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._with_review_input(request))


def review_record(verdict: Any, **fields: Any) -> dict[str, Any]:
    if verdict is None:
        return {"id": uuid.uuid4().hex, "verdict": "error", "issues": [], **fields}
    return {"id": uuid.uuid4().hex, "verdict": verdict.verdict, "checks": list(verdict.checks),
            "issues": [issue.model_dump() for issue in verdict.issues], **fields}


class ReviewRecordMiddleware(AgentMiddleware):
    """Inside the reviewer: record its verdict for the SQL it reviewed (state `semantic_validations`)."""

    state_schema = DeepAgentRuntimeState

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        sql, _ = parse_brief(messages[0].text if messages else "")
        verdict = state.get("structured_response")
        reason = None if verdict else "the reviewer ended without a verdict"
        return {"semantic_validations": [review_record(verdict, sql=normalize_sql(sql), reason=reason,
                                                       after_execution=len(_turn_executions(state)))]}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)


def build_validator(model: BaseChatModel):
    """The reviewer agent: facts-only input, catalog + date in context, no SQL tools, verdict via submit_verdict."""
    prompt = load_prompt("validator").text
    agent = create_agent(
        model=model,
        tools=[],  # it judges by reading; checks that need data are run by the main agent (needs_check)
        system_prompt=prompt,
        middleware=[
            ReviewInputMiddleware(),  # first: the date is then added to the built input, not to the brief
            DeepAgentContextMiddleware(),
            CatalogMiddleware(),
            PromptRefreshMiddleware("validator", prompt),
            *submit_fallback(VERDICT_TOOL),
            ReviewRecordMiddleware(),
        ],
        response_format=submission_strategy(SubmitVerdict),
        context_schema=DeepAgentContext,
        checkpointer=False,  # a fresh, unsaved state per review (never the main thread's checkpointer)
        name=VALIDATOR_NAME,
    )
    return agent.with_config(REVIEW_CONFIG)
