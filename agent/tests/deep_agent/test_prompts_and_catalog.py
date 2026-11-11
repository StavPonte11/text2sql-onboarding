"""Prompt registry (Langfuse-managed prompts), the deterministic catalog, and subagent state handling."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.config import settings
from deep_agent import observability
from deep_agent.agent import CLARIFICATION_PROMPT, create_deep_agent
from deep_agent.context import merge_executions
from deep_agent.middleware.catalog import CatalogLoadError
from deep_agent.prompts import registry as prompt_registry
from deep_agent.prompts.registry import PROMPT_KEYS, load_prompt, local_prompt
from tests.deep_agent.conftest import TEST_CATALOG, system_text

# ── deterministic catalog ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_is_in_every_model_call_but_not_in_history(scripted_model):
    model = scripted_model(AIMessage("", tool_calls=[{"name": "write_todos", "args": {"todos": []}, "id": "w"}]),
                           AIMessage("done"), AIMessage("done"))  # last: reply to the submit reminder
    result = await create_deep_agent(model=model).ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "c"}})
    assert len(model.seen) == 3
    assert all(TEST_CATALOG in system_text(seen) for seen in model.seen)
    assert all(TEST_CATALOG not in m.text for m in result["messages"])  # never duplicated into the message history
    assert "get_database_catalog" not in model.bound_tool_names[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("mcp down"), ""], ids=["unreachable", "empty"])
async def test_run_fails_clearly_without_a_catalog(scripted_model, fake_catalog, failure):
    if isinstance(failure, Exception):
        fake_catalog.get_catalog_prompt.side_effect = failure
    else:
        fake_catalog.get_catalog_prompt.return_value = failure
    model = scripted_model(AIMessage("done"))
    with pytest.raises(CatalogLoadError, match="catalog"):
        await create_deep_agent(model=model).ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "x"}})
    assert model.seen == []  # the model is never called without a catalog


@pytest.mark.asyncio
async def test_general_purpose_subagent_sees_the_catalog(policy_model):
    def policy(messages):
        roles = [m.type for m in messages]
        if messages and messages[0].type == "human" and "explore" in messages[0].text:  # inside the subagent
            return AIMessage("subagent brief")
        if "tool" not in roles:
            return AIMessage("", tool_calls=[{"name": "task", "id": "t", "args": {
                "description": "explore the order tables", "subagent_type": "general-purpose"}}])
        return AIMessage("done")

    model = policy_model(policy)
    await create_deep_agent(model=model).ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "gp"}})
    sub_calls = [seen for seen in model.seen if any(m.type == "human" and "explore" in m.text for m in seen)]
    assert sub_calls, "the subagent was never called"
    assert all(TEST_CATALOG in system_text(seen) for seen in sub_calls)


# ── subagent state round trips ───────────────────────────────────────────────

def test_merge_executions_is_idempotent_for_subagent_round_trips():
    a = {"query_id": "q1", "executed_sql": "SELECT 1"}
    b = {"query_id": "q2", "executed_sql": "SELECT 2"}
    # Subagent returns parent's history plus one new record.
    assert merge_executions([a], [a, b]) == [a, b]
    assert merge_executions(None, [a, a]) == [a]


# ── prompt registry ──────────────────────────────────────────────────────────

def test_every_prompt_has_a_packaged_fallback():
    for key in PROMPT_KEYS:
        assert local_prompt(key).strip()


def test_disabled_langfuse_uses_packaged_prompt():
    p = load_prompt("system")
    assert p.source == "local" and p.text == local_prompt("system") and p.client is None


@pytest.fixture
def langfuse_enabled(monkeypatch):
    monkeypatch.setattr(settings, "DEEP_AGENT_LANGFUSE_PROMPTS", True)
    prompt_registry._misses.clear()
    yield
    prompt_registry._misses.clear()


def _served(text, version=3, fallback=False):
    return SimpleNamespace(prompt=text, version=version, is_fallback=fallback)


def test_langfuse_prompt_by_label(langfuse_enabled, monkeypatch):
    monkeypatch.setattr(settings, "DEEP_AGENT_PROMPT_LABEL", "staging")
    fake = MagicMock(get_prompt=MagicMock(return_value=_served("served v3")))
    with patch("agent.langfuse_client.langfuse_client", fake):
        p = load_prompt("system")
    assert (p.source, p.text, p.version) == ("langfuse", "served v3", 3)
    args, kwargs = fake.get_prompt.call_args
    assert args == ("text2sql/deep_agent/system",)
    assert kwargs["label"] == "staging" and kwargs["fallback"] == local_prompt("system")


@pytest.mark.parametrize("behaviour", ["fallback", "raises"])
def test_langfuse_unavailable_falls_back(langfuse_enabled, behaviour):
    fake = MagicMock()
    if behaviour == "fallback":
        fake.get_prompt.return_value = _served(local_prompt("system"), version=None, fallback=True)
    else:
        fake.get_prompt.side_effect = RuntimeError("404 prompt not found")
    with patch("agent.langfuse_client.langfuse_client", fake):
        p = load_prompt("system")
    assert p.source == "local" and p.text == local_prompt("system")


@pytest.mark.asyncio
async def test_prompt_change_rebuilds_the_agent_on_the_next_request(langfuse_enabled):
    from deep_agent import service

    served = {"system": "system v1", "validator": "validator v1"}
    fake = MagicMock(get_prompt=MagicMock(side_effect=lambda name, **k: _served(served[name.rsplit("/", 1)[1]])))
    builds = []

    def fake_build(**kwargs):
        builds.append(dict(served))
        return object()

    service.reset_for_tests()
    with patch("agent.langfuse_client.langfuse_client", fake), \
         patch("deep_agent.agent.create_deep_agent", fake_build), \
         patch.object(service, "build_checkpointer", MagicMock(side_effect=lambda: _async(object()))):
        first = await service.get_deep_agent()
        assert await service.get_deep_agent() is first  # unchanged prompts: same agent
        served["validator"] = "validator v2"
        second = await service.get_deep_agent()
    service.reset_for_tests()
    assert second is not first and builds == [{"system": "system v1", "validator": "validator v1"},
                                              {"system": "system v1", "validator": "validator v2"}]


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_a_run_keeps_the_prompt_it_started_with(scripted_model, langfuse_enabled):
    served = {"text": "You are the deep analyst prompt v4."}
    fake = MagicMock(get_prompt=MagicMock(side_effect=lambda *a, **k: _served(served["text"])))
    model = scripted_model(AIMessage("one"), AIMessage("one"))  # answer + reminder reply
    with patch("agent.langfuse_client.langfuse_client", fake):
        agent = create_deep_agent(model=model)
        served["text"] = "You are the NEW deep analyst prompt v5."  # label moved while the agent exists
        await agent.ainvoke({"messages": [("user", "a")]}, {"configurable": {"thread_id": "p1"}})
    prompts = [system_text(seen) for seen in model.seen]
    assert all(p.startswith("You are the deep analyst prompt v4.") for p in prompts)
    assert "Skills System" in prompts[0]


def test_sync_script_pushes_every_prompt_with_label():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "sync_deep_agent_prompts.py"
    spec = importlib.util.spec_from_file_location("sync_prompts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = MagicMock()
    pushed = module.sync(fake, label="staging", dry_run=False)
    assert pushed == [f"text2sql/deep_agent/{k}" for k in PROMPT_KEYS]
    for call, key in zip(fake.create_prompt.call_args_list, PROMPT_KEYS):
        assert call.kwargs["name"] == f"text2sql/deep_agent/{key}"
        assert call.kwargs["prompt"] == local_prompt(key)
        assert call.kwargs["labels"] == ["staging"] and call.kwargs["type"] == "text"
    assert module.sync(MagicMock(), label="staging", dry_run=True) == pushed
    json.dumps(pushed)


def test_missing_prompt_is_not_refetched_on_every_call(langfuse_enabled):
    """A 404 / fallback must not cost a Langfuse round trip on every model call."""
    fake = MagicMock(get_prompt=MagicMock(return_value=_served(local_prompt("system"), version=None, fallback=True)))
    with patch("agent.langfuse_client.langfuse_client", fake):
        for _ in range(5):
            assert load_prompt("system").source == "local"
    assert fake.get_prompt.call_count == 1


def test_miss_expires_after_ttl(langfuse_enabled, monkeypatch):
    fake = MagicMock(get_prompt=MagicMock(side_effect=[RuntimeError("down"), _served("now served", version=2)]))
    with patch("agent.langfuse_client.langfuse_client", fake):
        assert load_prompt("system").source == "local"
        prompt_registry._misses["system"] = 0.0  # TTL elapsed
        assert load_prompt("system").text == "now served"


@pytest.mark.asyncio
async def test_langfuse_prompt_keeps_the_clarification_suffix(scripted_model, langfuse_enabled):
    fake = MagicMock(get_prompt=MagicMock(side_effect=lambda *a, **k: _served("NEW system prompt v5.")))
    model = scripted_model(AIMessage("done"), AIMessage("done"))
    with patch("agent.langfuse_client.langfuse_client", fake):
        agent = create_deep_agent(model=model, clarifications_enabled=True)
        await agent.ainvoke({"messages": [("user", "a")]}, {"configurable": {"thread_id": "s"}})
    prompt = system_text(model.seen[0])
    assert prompt.startswith("NEW system prompt v5." + CLARIFICATION_PROMPT)
