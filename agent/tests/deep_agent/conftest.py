from collections.abc import Callable

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(GenericFakeChatModel):
    """Fake chat model that replays scripted AIMessages and records every prompt it receives."""

    seen: list[list[BaseMessage]] = []
    bound_tool_names: list[list[str]] = []
    bound_tools: list[list[dict]] = []  # full schemas, as the model would see them

    def bind_tools(self, tools, **kwargs):  # deepagents binds tools; the script decides the calls
        from langchain_core.utils.function_calling import convert_to_openai_tool

        self.bound_tool_names.append([getattr(t, "name", None) or t.get("name") for t in tools])
        self.bound_tools.append([convert_to_openai_tool(t) for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def scripted_model():
    def _make(*replies: AIMessage) -> ScriptedChatModel:
        model = ScriptedChatModel(messages=iter(replies))
        model.seen = []
        model.bound_tool_names = []
        model.bound_tools = []
        return model

    return _make


def system_text(messages: list[BaseMessage]) -> str:
    return "\n".join(m.text for m in messages if m.type == "system")


class PolicyChatModel(ScriptedChatModel):
    """Fake chat model whose next reply is computed from the conversation so far.

    Safe to share across concurrent threads (unlike a scripted iterator).
    """

    policy: Callable[[list[BaseMessage]], AIMessage] | None = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.policy(messages))])


@pytest.fixture
def policy_model():
    def _make(policy: Callable[[list[BaseMessage]], AIMessage]) -> PolicyChatModel:
        model = PolicyChatModel(messages=iter([]), policy=policy)
        model.seen = []
        model.bound_tool_names = []
        model.bound_tools = []
        return model

    return _make


TEST_CATALOG = '# Database Schema\n\n## Columns\n"catalog"."schema"."orders": Orders\n  - "status" (varchar) [semantic_type=categorical]'


@pytest.fixture(autouse=True)
def no_semantic_validation(monkeypatch):
    """Scripted models answer the main agent only; tests that cover the reviewer turn it on explicitly."""
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_SEMANTIC_VALIDATION", "off")


@pytest.fixture(autouse=True)
def packaged_prompts(monkeypatch):
    """Prompts come from the packaged .md files; tests that cover Langfuse prompts turn it on explicitly."""
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_LANGFUSE_PROMPTS", False)


@pytest.fixture(autouse=True)
def fake_catalog(monkeypatch):
    """Every agent run loads the connection's catalog (CatalogMiddleware); never hit the real Jeen service in tests.

    Tests that need a specific catalog or a failure override `fake_catalog.get_catalog_prompt`.
    """
    from unittest.mock import AsyncMock, MagicMock

    import deep_agent.middleware.catalog as catalog_module

    client = MagicMock(get_catalog_prompt=AsyncMock(return_value=TEST_CATALOG))
    monkeypatch.setattr(catalog_module, "get_jeen_metadata_client", lambda: client)
    return client
