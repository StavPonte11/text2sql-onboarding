"""
Agent logic and LangGraph harness for the Deep Agent.

The system prompt is served by deep_agent.prompts.registry (Langfuse `deep-agent/system` when
DEEP_AGENT_LANGFUSE_PROMPTS is on, else the packaged prompts/system.md). The connection's catalog
is added to every model call by CatalogMiddleware (deep_agent.middleware.catalog).
"""


from deepagents import create_deep_agent as create_harness_agent
from deepagents.middleware.subagents import DEFAULT_GENERAL_PURPOSE_DESCRIPTION, DEFAULT_SUBAGENT_PROMPT
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent.config import settings
from agent.llm import get_llm
from deep_agent.answer.middleware import AnswerVerificationMiddleware, SubmitApprovalMiddleware, submit_fallback
from deep_agent.answer.schema import TOOL_NAME, answer_schema, submission_strategy
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware
from deep_agent.middleware.catalog import CatalogMiddleware
from deep_agent.middleware.tool_filter import BuiltinToolFilterMiddleware
from deep_agent.prompts.registry import PromptRefreshMiddleware, load_prompt, local_prompt
from deep_agent.resources import build_resources
from deep_agent.tools import DEEP_AGENT_TOOLS
from deep_agent.validator.modes import ON_DEMAND_PROMPT, REVIEW_PROMPT, ReviewTurnMiddleware, validator_subagent
from deep_agent.validator.reviewer import ValidationMode

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
        semantic_validation: Override DEEP_AGENT_SEMANTIC_VALIDATION: always | on_demand | off (tests).
        validator_model: Model for the independent reviewer; defaults to the agent's model.
    """
    if clarifications_enabled is None:
        clarifications_enabled = settings.DEEP_AGENT_CLARIFICATIONS_ENABLED
    mode = semantic_validation or settings.DEEP_AGENT_SEMANTIC_VALIDATION
    llm = model or get_llm()
    memory = checkpointer or MemorySaver()
    resources = build_resources()
    review_required = mode == "always"            # submit needs a validator pass
    has_reviewer = mode in ("always", "on_demand")  # the semantic-validator subagent is available
    review_prompt = {"always": REVIEW_PROMPT, "on_demand": ON_DEMAND_PROMPT}.get(mode, "")
    suffix = (CLARIFICATION_PROMPT if clarifications_enabled else "") + review_prompt
    system_prompt = load_prompt("system").text + suffix
    subagents = [_general_purpose_subagent()]
    if has_reviewer:
        subagents.append(validator_subagent(validator_model or llm, mode))

    agent = create_harness_agent(
        model=llm,
        tools=DEEP_AGENT_TOOLS,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=[
            DeepAgentContextMiddleware(),
            CatalogMiddleware(),
            PromptRefreshMiddleware("system", system_prompt, suffix=suffix),
            *submit_fallback(TOOL_NAME),
            AnswerVerificationMiddleware(has_reviewer),
            *([ReviewTurnMiddleware()] if has_reviewer else []),
            SubmitApprovalMiddleware(review_required, settings.DEEP_AGENT_VALIDATOR_MAX_ROUNDS),
            BuiltinToolFilterMiddleware(),
        ],
        response_format=submission_strategy(answer_schema(clarifications_enabled, review_required)),
        context_schema=DeepAgentContext,
        checkpointer=memory,
        backend=resources.backend,
        skills=resources.skills,
        permissions=resources.permissions,
    )

    return agent
