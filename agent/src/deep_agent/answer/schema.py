"""
The `submit_answer` tool: the format the model finishes with (LangChain ToolStrategy structured output).
"""

from typing import Literal

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field, create_model

from agent.config import settings

TOOL_NAME = "submit_answer"
# Content of the tool message for an accepted submission (anything else there is an error or a review's feedback).
ANSWER_RECEIVED = "Answer received."
_SUBMIT_DOC = "Submit your final answer. Call this exactly once, when you are done — it ends your work."


class _AnswerFields(BaseModel):
    sql: str = Field(
        min_length=1,
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
    description="Only when the semantic-validator failed this SQL and you submit it anyway: why it is correct "
                "despite the review (Hebrew).",
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

    With semantic validation "always", it also has `validation_response` (to dispute a failed review).
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
