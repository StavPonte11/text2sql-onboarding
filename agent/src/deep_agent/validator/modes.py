"""
How the reviewer (deep_agent.validator.reviewer) is wired into the agent — DEEP_AGENT_SEMANTIC_VALIDATION:

- always     the `semantic-validator` subagent is available through `task`, and submit_answer is accepted
             only for SQL it passed (or that the agent disputes) — see SubmitApprovalMiddleware
             (deep_agent.answer.middleware).
- on_demand  the subagent is available and the agent decides whether to call it; submit_answer only requires
             that the SQL ran (the training-mode default, deep_agent.run_mode).
- off        no reviewer; submit_answer still requires that the SQL ran.

Whenever the reviewer exists, the agent sees the same description and REVIEW_PROMPT (call it before submitting),
so serving and training differ only in what is enforced, not in what the model is told.

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
    "It reads a SQL query against the user's request and the catalog (it doesn't run queries) and returns JSON "
    "{verdict: pass|fail|needs_check, issues: [{problem, evidence}], checks: [...]}. Send exactly two things in the description: "
    "the SQL in a ```sql block, and your interpretation choices as a list of lines starting with '- '. Nothing else "
    "is read: the reviewer gets the user's messages and this SQL's result rows itself."
)
VALIDATOR_DESCRIPTION = "Independent reviewer — call it before submit_answer. " + _HOW_TO_SEND

# Appended to the main system prompt whenever the reviewer exists.
REVIEW_PROMPT = """
## Before you submit (required)

1. The final SQL must have run successfully with `execute_trino_query` in this turn — check its rows.
2. Ask the `semantic-validator` to review it: `task` with subagent_type `semantic-validator`, the SQL in a ```sql block and your assumptions as `- ` lines.
3. Pass → call `submit_answer` with exactly that SQL (any change needs a new review). Fail → fix the SQL and repeat 1–2; if you are sure the reviewer is wrong, you may submit the same SQL with `validation_response` explaining why. Needs check → run exactly the checks it names with `execute_trino_query`, then ask for review again (it sees their results).

`submit_answer` is refused for SQL that hasn't run successfully in this turn.
"""

class ReviewTurnMiddleware(AgentMiddleware):
    """Start every turn with no review records, and give the reviewer the user's messages."""

    state_schema = DeepAgentRuntimeState

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        user_messages = [m.text for m in state.get("messages") or [] if is_user_message(m) and m.text.strip()]
        return {"semantic_validations": None, "user_messages": user_messages}  # None resets (context.merge_reviews)

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)


def validator_subagent(model: BaseChatModel) -> dict[str, Any]:
    """The reviewer as a deepagents CompiledSubAgent (called through `task`)."""
    return {"name": VALIDATOR_NAME, "description": VALIDATOR_DESCRIPTION, "runnable": build_validator(model)}
