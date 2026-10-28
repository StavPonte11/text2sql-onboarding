"""
Agent logic and LangGraph harness for the Deep Agent.
"""

from pathlib import Path

from deepagents import create_deep_agent as create_harness_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent.llm import get_llm
from agent.config import settings
from deep_agent.answer import AnswerVerificationMiddleware, answer_schema
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware
from deep_agent.resources import build_resources
from deep_agent.tool_filter import BuiltinToolFilterMiddleware
from deep_agent.tools import DEEP_AGENT_TOOLS


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")

# Appended to the system prompt only when DEEP_AGENT_CLARIFICATIONS_ENABLED is on.
CLARIFICATION_PROMPT = """
If the question is genuinely ambiguous, set `needs_clarification` (a short Hebrew question to the user) instead of `sql`.
"""


def create_deep_agent(model: BaseChatModel | None = None, clarifications_enabled: bool | None = None):
    """
    Creates and returns a compiled LangGraph deep agent equipped with all exploration,
    enrichment, temporal, geospatial, and semantic validation tools.

    Invoke with a per-request context, e.g.
        agent.ainvoke(inputs, config, context=DeepAgentContext(connection_id=89, user_id="u1"))

    Args:
        model: Chat model override (tests); defaults to the configured LLM.
        clarifications_enabled: Override DEEP_AGENT_CLARIFICATIONS_ENABLED (tests).
    """
    if clarifications_enabled is None:
        clarifications_enabled = settings.DEEP_AGENT_CLARIFICATIONS_ENABLED
    llm = model or get_llm()
    memory = MemorySaver()
    resources = build_resources()

    agent = create_harness_agent(
        model=llm,
        tools=DEEP_AGENT_TOOLS,
        system_prompt=SYSTEM_PROMPT + CLARIFICATION_PROMPT if clarifications_enabled else SYSTEM_PROMPT,
        middleware=[DeepAgentContextMiddleware(), AnswerVerificationMiddleware(), BuiltinToolFilterMiddleware()],
        response_format=ToolStrategy(answer_schema(clarifications_enabled)),
        context_schema=DeepAgentContext,
        checkpointer=memory,
        backend=resources.backend,
        skills=resources.skills,
        permissions=resources.permissions,
    )

    return agent
