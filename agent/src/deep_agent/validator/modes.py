"""
How the reviewer (deep_agent.validator.reviewer) is used — DEEP_AGENT_SEMANTIC_VALIDATION:

- always     SemanticValidationMiddleware reviews every accepted submit_answer call; on fail the model
             gets the feedback and fixes the SQL or disputes it (see the package docstring)
- on_demand  validator_subagent(): the reviewer as a `task` subagent the model may call
- (both)     ReviewTurnMiddleware starts every turn with no review records
"""

import logging
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage

from deep_agent.answer.schema import ANSWER_RECEIVED, TOOL_NAME, last_submission
from deep_agent.answer.verification import normalize_sql
from deep_agent.context import DeepAgentRuntimeState
from deep_agent.validator.reviewer import VALIDATOR_NAME, build_validator, review_record, review_request

logger = logging.getLogger(__name__)

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


class ReviewTurnMiddleware(AgentMiddleware):
    """Start every turn with no reviews (both modes)."""

    state_schema = DeepAgentRuntimeState

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return {"semantic_validations": None}  # None resets (context.merge_reviews)

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)


def validator_subagent(model: BaseChatModel) -> dict[str, Any]:
    """The on-demand reviewer as a deepagents CompiledSubAgent (called through `task`)."""
    return {"name": VALIDATOR_NAME, "description": ON_DEMAND_DESCRIPTION, "runnable": build_validator(model, on_demand=True)}



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
        record = review_record("always", verdict, sql=normalize_sql(answer.sql),
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
