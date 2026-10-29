"""
Middleware around the final answer:

- AnswerRecoveryMiddleware      answer written as JSON text instead of a submit_answer call -> the real call
- SubmitReminderMiddleware      the model stopped without its submit call -> one reminder per turn
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

from deep_agent.answer.schema import ANSWER_RECEIVED, last_submission
from deep_agent.answer.verification import validation_summary, verify_answer
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
