"""
Middleware around the final answer:

- AnswerRecoveryMiddleware      answer written as JSON text instead of a submit_answer call -> the real call
- SubmitReminderMiddleware      the model stopped without its submit call -> one reminder per turn
                                (these two are the submit fallback; off with DEEP_AGENT_STRICT_SUBMIT)
- SubmitApprovalMiddleware      submit_answer is accepted only for SQL that ran this turn and that the
                                semantic-validator passed (or that the agent disputes); it checks records, it
                                doesn't judge
- AnswerVerificationMiddleware  each turn starts clean; invalid submissions are retried; at the end of
                                the turn the answer is verified (deep_agent.answer.verification) -> final_result
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from agent.config import settings
from deep_agent.answer.schema import ANSWER_RECEIVED, last_submission
from deep_agent.answer.verification import match_execution, normalize_sql, validation_summary, verify_answer
from deep_agent.context import REMINDER_NAME, current_turn

logger = logging.getLogger(__name__)


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


def submit_fallback(tool_name: str) -> list[AgentMiddleware]:
    """Recovery + reminder for a model that doesn't call its submit tool — none with DEEP_AGENT_STRICT_SUBMIT."""
    if settings.DEEP_AGENT_STRICT_SUBMIT:
        return []
    return [AnswerRecoveryMiddleware(), SubmitReminderMiddleware(tool_name)]


NOT_RUN = ("Not submitted: this SQL has not run successfully in this turn. Run it with execute_trino_query, check "
           "its rows, have the semantic-validator review it, then submit it.")
NOT_REVIEWED = ("Not submitted: the semantic-validator has not reviewed this exact SQL (a SQL that passed review "
                "and was then changed counts as not reviewed; formatting differences don't matter). Submit exactly the "
                "SQL that passed, or send this one to the semantic-validator (task) — the SQL in a ```sql block and "
                "your assumptions as '- ' lines — and submit after a pass.")


def _failed_text(issues: list[dict[str, str]]) -> str:
    listed = "\n".join(f"{n}. {i['problem']}\n   Evidence: {i['evidence']}" for n, i in enumerate(issues, 1))
    return ("Not submitted: the semantic-validator failed this SQL:\n" f"{listed}\n\n"
            "Fix it (run it, have it reviewed, submit), or — if you are sure it is right — submit the same SQL "
            "with `validation_response` explaining why.")


class SubmitApprovalMiddleware(AgentMiddleware):
    """Accept submit_answer only when the SQL has both approvals — recorded before the submit.

    1. ran: the SQL matches a successful execute_trino_query in this turn (a sample counts)
    2. reviewed (when review is required): the latest semantic-validator verdict for this SQL is a pass —
       or a fail and the agent gives `validation_response` (a dispute, recorded and shown to the user).
       If DEEP_AGENT_VALIDATOR_MAX_ROUNDS reviews already ran this turn, the SQL is accepted as not validated.

    It only looks the records up; the judging happened before (the execution, the reviewer). A refused
    submission's tool message says why and the model is sent back.
    """

    def __init__(self, require_review: bool, max_rounds: int | None = None) -> None:
        super().__init__()
        self.require_review = require_review
        self.max_rounds = max_rounds

    @staticmethod
    def _refuse(submission: ToolMessage, reason: str) -> dict[str, Any]:
        refusal = ToolMessage(content=reason, tool_call_id=submission.tool_call_id, name=submission.name,
                              id=submission.id)  # same id: replaces "Answer received."
        return {"messages": [refusal], "structured_response": None, "jump_to": "model"}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        submission = last_submission(state.get("messages") or [])
        answer = state.get("structured_response")
        if submission is None or submission.text != ANSWER_RECEIVED or answer is None:
            return None
        if getattr(answer, "needs_clarification", None) or not answer.sql:
            return None
        executed = (state.get("executed_queries") or [])[state.get("turn_execution_start") or 0:]
        if match_execution(answer.sql, executed) is None:
            return self._refuse(submission, NOT_RUN)
        if not self.require_review:
            return None
        sql = normalize_sql(answer.sql)
        records = list(state.get("semantic_validations") or [])
        reviews = [r for r in records if r.get("sql") == sql and r["verdict"] in ("pass", "fail")]
        if reviews and reviews[-1]["verdict"] == "pass":
            return None
        if reviews:  # failed
            if getattr(answer, "validation_response", None):
                return {"semantic_validations": [{"id": uuid.uuid4().hex, "verdict": "disputed", "sql": sql,
                                                  "issues": reviews[-1]["issues"],
                                                  "response": answer.validation_response}]}
            return self._refuse(submission, _failed_text(reviews[-1]["issues"]))
        done = sum(1 for r in records if r["verdict"] in ("pass", "fail"))
        if self.max_rounds is not None and done >= self.max_rounds:
            return {"semantic_validations": [{"id": uuid.uuid4().hex, "verdict": "skipped", "sql": sql, "issues": [],
                                              "reason": f"review limit reached ({self.max_rounds})"}]}
        return self._refuse(submission, NOT_REVIEWED)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


class AnswerVerificationMiddleware(AgentMiddleware):
    """Bind each turn's structured answer to SQL executed in that turn and its real rows.

    before_agent clears the previous turn's structured answer (it would otherwise be reported
    again on a turn that ends without a new one) and marks where this turn's executions start,
    so an answer can only match SQL that ran in its own turn.

    after_model sends the model back after an invalid submit_answer call (arguments that fail the
    schema). LangChain would retry on its own only while `structured_response` has never been set
    in the thread; from the first reset on, it ends the run instead.

    after_agent keeps the answer only as plain data in final_result and drops the pydantic object:
    checkpoints can't restore the dynamically built `submit_answer` class.

    review_required: whether final_result gets a review summary (with review off it is None).
    """

    def __init__(self, review_required: bool = False) -> None:
        super().__init__()
        self.review_required = review_required

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
        records = state.get("semantic_validations") or [] if self.review_required else None
        final["semantic_validation"] = validation_summary(records, final.get("sql"))
        return {"final_result": final, "structured_response": None}

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.after_agent, state, runtime)
