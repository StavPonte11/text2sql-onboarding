"""
Agent logic and LangGraph harness for the Deep Agent.
"""

from pathlib import Path

from deepagents import create_deep_agent as create_harness_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent.config import settings
from agent.llm import get_llm
from deep_agent.answer import AnswerVerificationMiddleware, DeepAgentAnswer
from deep_agent.context import DeepAgentContext, DeepAgentContextMiddleware
from deep_agent.limits import build_limit_middleware
from deep_agent.resources import build_resources
from deep_agent.tools import DEEP_AGENT_TOOLS


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")


def _default_llm() -> BaseChatModel:
    """Configured LLM with a deep-agent request timeout; retries are owned by ModelRetryMiddleware."""
    return get_llm().model_copy(update={"request_timeout": settings.DEEP_AGENT_LLM_TIMEOUT_S, "max_retries": 0})


def create_deep_agent(model: BaseChatModel | None = None):
    """
    Creates and returns a compiled LangGraph deep agent equipped with all exploration,
    enrichment, temporal, geospatial, and semantic validation tools.

    Invoke with a per-request context, e.g.
        agent.ainvoke(inputs, config, context=DeepAgentContext(connection_id=89, user_id="u1"))

    Args:
        model: Chat model override (tests); defaults to the configured LLM.
    """
    llm = model or _default_llm()
    memory = MemorySaver()
    resources = build_resources()

    agent = create_harness_agent(
        model=llm,
        tools=DEEP_AGENT_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=[DeepAgentContextMiddleware(), AnswerVerificationMiddleware(), *build_limit_middleware()],
        response_format=ToolStrategy(DeepAgentAnswer),
        context_schema=DeepAgentContext,
        checkpointer=memory,
        backend=resources.backend,
        memory=resources.memory,
        skills=resources.skills,
        permissions=resources.permissions,
    )

    return agent
