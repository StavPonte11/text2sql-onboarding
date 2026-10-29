"""
Agent logic and LangGraph harness for the Deep Agent.

The system prompt is served by deep_agent.prompt_registry (Langfuse `deep-agent/system` when
DEEP_AGENT_LANGFUSE_PROMPTS is on, else the packaged prompts/system.md). The connection's catalog
is added to every model call by CatalogMiddleware (deep_agent.catalog).
"""

from deepagents import create_deep_agent as create_harness_agent
from deepagents.middleware.subagents import DEFAULT_GENERAL_PURPOSE_DESCRIPTION, DEFAULT_SUBAGENT_PROMPT
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent.config import settings
from agent.llm import get_llm
from deep_agent.answer import (
    TOOL_NAME,
    AnswerRecoveryMiddleware,
    AnswerVerificationMiddleware,
    SubmitReminderMiddleware,
    answer_schema,
    submission_strategy,
)
from deep_agent.catalog import CatalogMiddleware
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware
from deep_agent.prompt_registry import PromptRefreshMiddleware, load_prompt, local_prompt
from deep_agent.resources import build_resources
from deep_agent.tool_filter import BuiltinToolFilterMiddleware
from deep_agent.tools import DEEP_AGENT_TOOLS
from deep_agent.validator import (
    ReviewTurnMiddleware,
    SemanticValidationMiddleware,
    ValidationMode,
    validator_subagent,
)

# Packaged system prompt (the Langfuse-managed version, when enabled, is loaded per agent build and refreshed per call).
SYSTEM_PROMPT = local_prompt("system")

# Appended to the system prompt only when DEEP_AGENT_CLARIFICATIONS_ENABLED is on.
CLARIFICATION_PROMPT = """
If the question is genuinely ambiguous, set `needs_clarification` (a short Hebrew question to the user) instead of `sql`.
"""


def _general_purpose_subagent() -> dict:
    """deepagents' default `task` subagent, with our request context and the catalog it can no longer fetch itself."""
    return {
        "name": "general-purpose",
        "description": DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
        "system_prompt": DEFAULT_SUBAGENT_PROMPT,
        "middleware": [DeepAgentContextMiddleware(), CatalogMiddleware(), BuiltinToolFilterMiddleware()],
    }


def create_deep_agent(
    model: BaseChatModel | None = None,
    clarifications_enabled: bool | None = None,
    checkpointer=None,
    semantic_validation: ValidationMode | None = None,
    validator_model: BaseChatModel | None = None,
):
    """
    Creates and returns a compiled LangGraph deep agent.

    Invoke with a per-request context, e.g.
        agent.ainvoke(inputs, config, context=DeepAgentContext(connection_id=89, user_id="u1"))

    Args:
        model: Chat model override (tests); defaults to the configured LLM.
        clarifications_enabled: Override DEEP_AGENT_CLARIFICATIONS_ENABLED (tests).
        checkpointer: Thread persistence (e.g. AsyncPostgresSaver); defaults to an in-memory saver.
        semantic_validation: Override DEEP_AGENT_SEMANTIC_VALIDATION: on_demand | always | off (tests).
        validator_model: Model for the independent reviewer; defaults to the agent's model.
    """
    if clarifications_enabled is None:
        clarifications_enabled = settings.DEEP_AGENT_CLARIFICATIONS_ENABLED
    mode = semantic_validation or settings.DEEP_AGENT_SEMANTIC_VALIDATION
    llm = model or get_llm()
    memory = checkpointer or MemorySaver()
    resources = build_resources()
    suffix = CLARIFICATION_PROMPT if clarifications_enabled else ""
    system_prompt = load_prompt("system").text + suffix
    reviewer = validator_model or llm
    subagents = [_general_purpose_subagent()]
    review_middleware = []
    if mode == "on_demand":
        subagents.append(validator_subagent(reviewer))
        review_middleware = [ReviewTurnMiddleware()]
    elif mode == "always":
        review_middleware = [ReviewTurnMiddleware(),
                             SemanticValidationMiddleware(reviewer, settings.DEEP_AGENT_VALIDATOR_MAX_ROUNDS)]

    agent = create_harness_agent(
        model=llm,
        tools=DEEP_AGENT_TOOLS,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=[
            DeepAgentContextMiddleware(),
            CatalogMiddleware(),
            PromptRefreshMiddleware("system", system_prompt, suffix=suffix),
            AnswerRecoveryMiddleware(),
            SubmitReminderMiddleware(TOOL_NAME),
            AnswerVerificationMiddleware(),
            *review_middleware,
            BuiltinToolFilterMiddleware(),
        ],
        response_format=submission_strategy(answer_schema(clarifications_enabled, mode == "always")),
        context_schema=DeepAgentContext,
        checkpointer=memory,
        backend=resources.backend,
        skills=resources.skills,
        permissions=resources.permissions,
    )

    return agent
