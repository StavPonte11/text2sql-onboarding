"""
Independent semantic review of the Deep Agent's answers.

The reviewer is a separate agent with a fresh context and read-only diagnostic tools. It gets
the user's request, the SQL, and (usually) the result rows and stated assumptions — never the
main agent's reasoning, so it can't simply agree with it — and returns `submit_verdict`
(pass / fail + issues with evidence). DEEP_AGENT_SEMANTIC_VALIDATION picks how it is used:

on_demand
    A `semantic-validator` subagent the agent calls through `task` when it judges a review
    worthwhile (its description says when, and what to send). The verdict comes back to the
    agent as JSON; acting on it is up to the agent. Each review is recorded.

always (default)
    Every accepted `submit_answer` call is reviewed before the run can end:
    - pass -> the run ends normally;
    - fail -> the "Answer received." tool message is replaced by the review's feedback and the
      model is sent back. It either fixes the SQL and submits again (reviewed again), or submits
      the same SQL with `validation_response` — a dispute, which ends the loop. If it stops
      without resubmitting, its last (failed) answer stands, marked "failed".
    DEEP_AGENT_VALIDATOR_MAX_ROUNDS optionally limits reviews per turn (default: no limit).

off
    No reviewer.

Both modes record reviews in `semantic_validations`; the outcome for the final answer goes to
final_result["semantic_validation"] (deep_agent.answer.validation_summary). Semantic checks also
stay mandatory in the main agent's own prompt; the reviewer is an extra, independent check.
"""

import logging
import re
import uuid
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field, create_model, model_validator

from deep_agent.answer import (
    ANSWER_RECEIVED,
    TOOL_NAME,
    AnswerRecoveryMiddleware,
    SubmitReminderMiddleware,
    last_submission,
    match_execution,
    normalize_sql,
    submission_strategy,
)
from deep_agent.catalog import CatalogMiddleware
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware, DeepAgentRuntimeState, is_user_message
from deep_agent.prompt_registry import PromptRefreshMiddleware, load_prompt
from deep_agent.tools import (
    execute_trino_query,
    get_column_distinct_values,
    get_column_profile,
    get_sample_rows,
    search_column_values,
)

logger = logging.getLogger(__name__)

ValidationMode = Literal["on_demand", "always", "off"]

VERDICT_TOOL = "submit_verdict"
VALIDATOR_NAME = "semantic-validator"
REVIEW_CONFIG = {"run_name": VALIDATOR_NAME, "tags": [VALIDATOR_NAME]}  # the tag marks the reviewer's nested runs
VALIDATOR_TOOLS = [execute_trino_query, get_sample_rows, get_column_distinct_values, search_column_values,
                   get_column_profile]

# What the main agent sees about the on-demand subagent (in the `task` tool's list of subagents).
ON_DEMAND_DESCRIPTION = (
    "Independent reviewer: checks whether a SQL answer really answers the user's request, using read-only "
    "diagnostic queries, and returns JSON {verdict: pass|fail, issues: [{problem, evidence}]}. It has not seen "
    "your work, which is the point. Worth it before submit_answer when the answer involves joins, aggregation "
    "or grouping, date ranges, several conditions, an interpretation you had to choose, 0 rows or a surprising "
    "number. Skip it for simple lookups you have already verified. In the task description send only: the "
    "user's messages verbatim (all of them, for a follow-up), the SQL to review in a ```sql block, its result "
    "rows, and your stated assumptions — not your reasoning or your conclusion. On fail, fix the SQL (and "
    "review again if the change is substantial), or keep it only if you can show why the issue is wrong."
)


class Issue(BaseModel):
    problem: str = Field(description="What is wrong and how to fix it (Hebrew).")
    evidence: str = Field(description="The query you ran and what it returned, or the exact part of the SQL / "
                                      "request that disagrees (Hebrew).")


class _VerdictFields(BaseModel):
    verdict: Literal["pass", "fail"] = Field(description="pass = the answer answers the request; fail = it doesn't.")
    issues: list[Issue] = Field(default_factory=list, description="Every problem found (required for fail).")

    @model_validator(mode="after")
    def _fail_needs_issues(self):
        if self.verdict == "fail" and not self.issues:
            raise ValueError("a fail verdict needs at least one issue with evidence")
        return self


SubmitVerdict = create_model(
    VERDICT_TOOL, __base__=_VerdictFields,
    __doc__="Submit your review verdict. Call this exactly once, when you are done — it ends your review.",
)

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _record(mode: ValidationMode, verdict: Any, **fields: Any) -> dict[str, Any]:
    if verdict is None:
        return {"id": uuid.uuid4().hex, "mode": mode, "verdict": "error", "issues": [], **fields}
    return {"id": uuid.uuid4().hex, "mode": mode, "verdict": verdict.verdict,
            "issues": [issue.model_dump() for issue in verdict.issues], **fields}


class ReviewRecordMiddleware(AgentMiddleware):
    """Inside the on-demand reviewer: record its verdict and which SQL it reviewed (```sql blocks of the brief)."""

    state_schema = DeepAgentRuntimeState

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        brief = messages[0].text if messages else ""
        sqls = [normalize_sql(block) for block in _SQL_BLOCK.findall(brief)]
        reason = None if state.get("structured_response") else "the reviewer ended without a verdict"
        return {"semantic_validations": [_record("on_demand", state.get("structured_response"),
                                                 sqls=[s for s in sqls if s], reason=reason)]}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)


class ReviewTurnMiddleware(AgentMiddleware):
    """Start every turn with no reviews (both modes)."""

    state_schema = DeepAgentRuntimeState

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"semantic_validations": None}  # None resets (context.merge_reviews)

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)


def build_validator(model: BaseChatModel, *, on_demand: bool = False):
    """The reviewer agent: catalog + date in context, read-only tools, verdict as structured output."""
    prompt = load_prompt("validator").text
    agent = create_agent(
        model=model,
        tools=VALIDATOR_TOOLS,
        system_prompt=prompt,
        middleware=[
            DeepAgentContextMiddleware(),
            CatalogMiddleware(),
            PromptRefreshMiddleware("validator", prompt),
            AnswerRecoveryMiddleware(),
            SubmitReminderMiddleware(VERDICT_TOOL),
            *([ReviewRecordMiddleware()] if on_demand else []),
        ],
        response_format=submission_strategy(SubmitVerdict),
        context_schema=DeepAgentContext,
        checkpointer=False,  # a fresh, unsaved state per review (never the main thread's checkpointer)
        name=VALIDATOR_NAME,
    )
    return agent.with_config(REVIEW_CONFIG)


def validator_subagent(model: BaseChatModel) -> dict[str, Any]:
    """The on-demand reviewer as a deepagents CompiledSubAgent (called through `task`)."""
    return {"name": VALIDATOR_NAME, "description": ON_DEMAND_DESCRIPTION, "runnable": build_validator(model, on_demand=True)}


# ── always: review every submitted answer ────────────────────────────────────

def _human_messages(messages: list) -> list[str]:
    return [m.text for m in messages if is_user_message(m) and m.text.strip()]


def _result_section(record: dict[str, Any] | None) -> str:
    if record is None:
        return "The analyst did not execute this exact SQL in this turn — run it yourself to see the result."
    note = " (truncated by the row ceiling: more rows exist)" if record.get("truncated") else ""
    lines = [f"Columns: {record['columns']}", f"Rows returned: {record['row_count']}{note}"]
    lines += [str(row) for row in record["rows"]]
    return "\n".join(lines)


def review_request(messages: list, answer: Any, executed: list[dict[str, Any]]) -> str:
    """The reviewer's only input: the user's messages, the answer as the user will see it, and its rows."""
    humans = _human_messages(messages)
    earlier = "\n".join(f"- {text}" for text in humans[:-1])
    parts = []
    if earlier:
        parts.append(f"## Earlier user messages (context)\n{earlier}")
    parts.append(f"## Current user request\n{humans[-1] if humans else ''}")
    assumptions = "\n".join(f"- {a}" for a in answer.assumptions) or "(none)"
    parts.append(
        f"## Answer to review\n```sql\n{answer.sql}\n```\n\n"
        f"Explanation shown to the user: {answer.explanation_he}\n\nStated assumptions:\n{assumptions}"
    )
    parts.append(f"## Result of this SQL\n{_result_section(match_execution(answer.sql, executed))}")
    return "\n\n".join(parts)


def feedback_text(issues: list[dict[str, str]]) -> str:
    listed = "\n".join(f"{n}. {i['problem']}\n   Evidence: {i['evidence']}" for n, i in enumerate(issues, 1))
    return (
        "An independent reviewer checked this answer against the user's request and found problems:\n"
        f"{listed}\n\n"
        f"Fix the SQL, execute it, and call {TOOL_NAME} again. If you are sure the answer is correct as it is, "
        f"call {TOOL_NAME} again with the same SQL and explain why in `validation_response`."
    )


class SemanticValidationMiddleware(AgentMiddleware):
    """`always` mode: reviews every accepted submit_answer call (see module docstring)."""

    state_schema = DeepAgentRuntimeState

    def __init__(self, model: BaseChatModel, max_rounds: int | None = None) -> None:
        super().__init__()
        self.model = model
        self.max_rounds = max_rounds
        self._validator = None

    @property
    def validator(self):
        if self._validator is None:
            self._validator = build_validator(self.model)
        return self._validator

    def _pending(self, state) -> tuple[Any, ToolMessage, list[dict[str, Any]]] | None:
        """(answer, its tool message, records) when the model just made an accepted SQL submission."""
        submission = last_submission(state.get("messages") or [])
        answer = state.get("structured_response")
        if submission is None or submission.text != ANSWER_RECEIVED or answer is None:
            return None
        if getattr(answer, "needs_clarification", None) or not answer.sql:
            return None
        return answer, submission, list(state.get("semantic_validations") or [])

    def _without_review(self, answer: Any, records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """A record that ends the loop without a review (dispute / round limit), else None."""
        sql = normalize_sql(answer.sql)
        previous = records[-1] if records else None
        if previous and previous["verdict"] == "fail" and previous["sql"] == sql:
            return {"id": uuid.uuid4().hex, "mode": "always", "verdict": "disputed", "sql": sql,
                    "issues": previous["issues"], "response": getattr(answer, "validation_response", None)}
        reviews = sum(1 for r in records if r["verdict"] in ("pass", "fail"))
        if self.max_rounds is not None and reviews >= self.max_rounds:
            return {"id": uuid.uuid4().hex, "mode": "always", "verdict": "skipped", "sql": sql, "issues": [],
                    "reason": f"review limit reached ({self.max_rounds})"}
        return None

    def _update(self, answer, submission, verdict, error: str | None) -> dict[str, Any]:
        record = _record("always", verdict, sql=normalize_sql(answer.sql),
                         reason=None if verdict is not None else error)
        if verdict is None:
            logger.warning("Semantic validation produced no verdict: %s", error)
        if verdict is None or verdict.verdict == "pass":
            return {"semantic_validations": [record]}
        feedback = ToolMessage(content=feedback_text(record["issues"]), tool_call_id=submission.tool_call_id,
                               name=submission.name, id=submission.id)  # same id: replaces "Answer received."
        return {"messages": [feedback], "semantic_validations": [record], "jump_to": "model"}

    def _review_input(self, state, answer) -> dict[str, Any]:
        executed = (state.get("executed_queries") or [])[state.get("turn_execution_start") or 0:]
        return {"messages": [HumanMessage(review_request(state.get("messages") or [], answer, executed))]}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        pending = self._pending(state)
        if pending is None:
            return None
        answer, submission, records = pending
        if (record := self._without_review(answer, records)) is not None:
            return {"semantic_validations": [record]}
        try:
            out = self.validator.invoke(self._review_input(state, answer), context=runtime.context)
            verdict, error = out.get("structured_response"), "the reviewer ended without a verdict"
        except Exception as exc:  # a broken review must not lose the answer
            verdict, error = None, f"{type(exc).__name__}: {exc}"
        return self._update(answer, submission, verdict, error)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        pending = self._pending(state)
        if pending is None:
            return None
        answer, submission, records = pending
        if (record := self._without_review(answer, records)) is not None:
            return {"semantic_validations": [record]}
        try:
            out = await self.validator.ainvoke(self._review_input(state, answer), context=runtime.context)
            verdict, error = out.get("structured_response"), "the reviewer ended without a verdict"
        except Exception as exc:  # a broken review must not lose the answer
            verdict, error = None, f"{type(exc).__name__}: {exc}"
        return self._update(answer, submission, verdict, error)
