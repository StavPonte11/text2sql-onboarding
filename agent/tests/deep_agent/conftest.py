from collections.abc import Iterator

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage


class ScriptedChatModel(GenericFakeChatModel):
    """Fake chat model that replays scripted AIMessages and records every prompt it receives."""

    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools, **kwargs):  # deepagents binds tools; the script decides the calls
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def scripted_model():
    def _make(*replies: AIMessage) -> ScriptedChatModel:
        model = ScriptedChatModel(messages=iter(replies))
        model.seen = []
        return model

    return _make


def system_text(messages: list[BaseMessage]) -> str:
    return "\n".join(m.text for m in messages if m.type == "system")
