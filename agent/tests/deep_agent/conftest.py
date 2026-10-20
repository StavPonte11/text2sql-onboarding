from collections.abc import Callable

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(GenericFakeChatModel):
    """Fake chat model that replays scripted AIMessages and records every prompt it receives."""

    seen: list[list[BaseMessage]] = []
    bound_tool_names: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):  # deepagents binds tools; the script decides the calls
        self.bound_tool_names.append([getattr(t, "name", None) or t.get("name") for t in tools])
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
        return model

    return _make


@pytest.fixture(autouse=True)
def _packaged_prompts(monkeypatch):
    """Unit tests use the packaged prompts; Langfuse prompt fetching is tested explicitly."""
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_LANGFUSE_PROMPTS", False)
