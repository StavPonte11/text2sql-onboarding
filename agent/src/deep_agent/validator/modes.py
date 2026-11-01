"""
How the reviewer (deep_agent.validator.reviewer) is wired into the agent — DEEP_AGENT_SEMANTIC_VALIDATION:

- always     the `semantic-validator` subagent is available through `task`, and submit_answer is accepted
             only for SQL it passed (or that the agent disputes) — see SubmitApprovalMiddleware
             (deep_agent.answer.middleware). REVIEW_PROMPT tells the agent the order of steps.
- on_demand  the subagent is available and the agent decides whether to call it (ON_DEMAND_PROMPT);
             submit_answer only requires that the SQL ran.
- off        no reviewer; submit_answer still requires that the SQL ran.

ReviewTurnMiddleware starts every turn with no review records and copies the user's messages into
state for the reviewer (subagents don't receive the conversation).
"""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel

from deep_agent.context import DeepAgentRuntimeState, is_user_message
from deep_agent.validator.reviewer import VALIDATOR_NAME, build_validator

# What the main agent sees about the subagent (in the `task` tool's list of subagents).
_HOW_TO_SEND = (
    "It checks whether a SQL query really answers the user's request, with read-only diagnostic queries, and "
    "returns JSON {verdict: pass|fail, issues: [{problem, evidence}]}. Send exactly two things in the description: "
    "the SQL in a ```sql block, and your interpretation choices as a list of lines starting with '- '. Nothing else "
    "is read: the reviewer gets the user's messages and this SQL's result rows itself."
)
VALIDATOR_DESCRIPTION = {
    "always": "Independent reviewer — required before submit_answer. " + _HOW_TO_SEND,
    "on_demand": ("Independent reviewer — worth it before submit_answer when the answer involves a join, aggregation "
                  "or grouping, several conditions, an interpretation you had to choose, 0 rows or a surprising "
                  "number. " + _HOW_TO_SEND),
}

# Appended to the main system prompt when review is required.
REVIEW_PROMPT = """
## Before you submit (required)

1. The final SQL must have run successfully with `execute_trino_query` in this turn — check its rows.
2. Ask the `semantic-validator` to review it: `task` with subagent_type `semantic-validator`, the SQL in a ```sql block and your assumptions as `- ` lines.
3. Pass → call `submit_answer` with exactly that SQL (any change needs a new review). Fail → fix the SQL and repeat 1–2. If you are sure the reviewer is wrong, you may submit the same SQL with `validation_response` explaining why.

`submit_answer` is refused for SQL that hasn't run, or that the reviewer hasn't passed.
"""

# Appended to the main system prompt when review is on demand.
ON_DEMAND_PROMPT = """
An independent reviewer is available: `task` with subagent_type `semantic-validator` (its description says when it is worth calling and what to send). `submit_answer` is refused for SQL that hasn't run successfully in this turn.
"""


class ReviewTurnMiddleware(AgentMiddleware):
    """Start every turn with no review records, and give the reviewer the user's messages."""

    state_schema = DeepAgentRuntimeState

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        user_messages = [m.text for m in state.get("messages") or [] if is_user_message(m) and m.text.strip()]
        return {"semantic_validations": None, "user_messages": user_messages}  # None resets (context.merge_reviews)

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)


def validator_subagent(model: BaseChatModel, mode: str = "always") -> dict[str, Any]:
    """The reviewer as a deepagents CompiledSubAgent (called through `task`)."""
    return {"name": VALIDATOR_NAME, "description": VALIDATOR_DESCRIPTION[mode], "runnable": build_validator(model)}
