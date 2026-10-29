"""
The reviewer agent: a separate agent with a fresh context (the user's messages, the SQL, its rows,
the stated assumptions and the catalog — never the main agent's reasoning), read-only diagnostic
tools, and a `submit_verdict` (pass / fail + issues with evidence). Its prompt is prompts/validator.md.
"""

import logging
import re
import uuid
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, create_model, model_validator

from deep_agent.answer.middleware import AnswerRecoveryMiddleware, SubmitReminderMiddleware
from deep_agent.answer.schema import submission_strategy
from deep_agent.answer.verification import match_execution, normalize_sql
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware, DeepAgentRuntimeState, is_user_message
from deep_agent.middleware.catalog import CatalogMiddleware
from deep_agent.prompts.registry import PromptRefreshMiddleware, load_prompt
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


def review_record(mode: ValidationMode, verdict: Any, **fields: Any) -> dict[str, Any]:
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
        return {"semantic_validations": [review_record("on_demand", state.get("structured_response"),
                                                 sqls=[s for s in sqls if s], reason=reason)]}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)


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
