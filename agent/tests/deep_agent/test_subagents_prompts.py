import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.config import settings
from deep_agent import observability, prompt_registry, tools
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext, merge_executions
from deep_agent.prompt_registry import PROMPT_KEYS, load_prompt, local_prompt
from deep_agent.subagents import SUBAGENT_TOOLS, build_subagents
from tests.deep_agent.conftest import system_text

HARNESS = {"read_file", "ls", "write_todos"}


def _role(messages) -> str:
    text = system_text(messages)
    for name in SUBAGENT_TOOLS:
        if f"You are {name}" in text:
            return name
    return "main"


def _delegating_policy(subagent: str, subagent_call: dict):
    """Main agent delegates once to `subagent`, which makes one tool call and reports back."""
    def policy(messages):
        role = _role(messages)
        tool_msgs = [m for m in messages if m.type == "tool"]
        if role == "main":
            if not tool_msgs:
                return AIMessage("", tool_calls=[{"name": "task", "id": "t1", "args": {
                    "subagent_type": subagent, "description": "resolve 'צרפת' in flights_table.launch_country"}}])
            return AIMessage("done")
        if not tool_msgs:
            return AIMessage("", tool_calls=[{**subagent_call, "id": "s1"}])
        return AIMessage("launch_country = 'France'")
    return policy


@pytest.mark.asyncio
async def test_subagent_gets_only_its_tools_context_and_date(policy_model):
    model = policy_model(_delegating_policy(
        "value-resolver", {"name": "search_column_values", "args": {"search_term": "צרפת", "column": "launch_country"}}))
    client = MagicMock(search_column_values=AsyncMock(return_value={"values": [{"value": "France"}]}))
    agent = create_deep_agent(model=model)
    with patch.object(tools, "get_jeen_metadata_client", return_value=client):
        result = await agent.ainvoke({"messages": [("user", "כמה טיסות מצרפת")]},
                                     {"configurable": {"thread_id": "sub"}}, context=DeepAgentContext(connection_id=7))

    # The subagent's model call offered exactly its allow-list (+ harness file/todo tools).
    sub_calls = [i for i, msgs in enumerate(model.seen) if _role(msgs) == "value-resolver"]
    offered = set(model.bound_tool_names[sub_calls[0]])
    assert offered - HARNESS == {tool.name for tool in SUBAGENT_TOOLS["value-resolver"]}
    assert "execute_trino_query" not in offered and "task" not in offered

    # Request context propagated into the subagent's tools; date middleware ran there too.
    assert client.search_column_values.await_args.kwargs["connection_id"] == 7
    first_human = next(m for m in model.seen[sub_calls[0]] if m.type == "human")
    assert first_human.text.startswith("[Today's date:")

    task_result = next(m for m in result["messages"] if m.type == "tool" and m.name == "task")
    assert "France" in task_result.text


@pytest.mark.asyncio
async def test_main_agent_task_tool_advertises_the_subagents(scripted_model):
    model = scripted_model(AIMessage("done"))
    await create_deep_agent(model=model).ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "adv"}})
    assert "task" in model.bound_tool_names[0]
    prompt = system_text(model.seen[0])
    for name in SUBAGENT_TOOLS:
        assert f"`{name}`" in prompt


def test_subagent_specs():
    specs = {s["name"]: s for s in build_subagents(MagicMock())}
    assert set(specs) == {"schema-scout", "value-resolver", "result-verifier"}
    assert [t.name for t in specs["result-verifier"]["tools"]] == ["execute_trino_query", "get_sample_rows"]
    assert all("model" not in s for s in specs.values())  # inherit main model by default
    assert all(s["system_prompt"] == local_prompt(s["name"]) for s in specs.values())


def test_subagent_model_override(monkeypatch):
    from deep_agent.agent import _default_llm

    monkeypatch.setattr(settings, "DEEP_AGENT_SUBAGENT_MODEL", "small-model")
    specs = build_subagents(_default_llm())
    assert {s["model"].model_name for s in specs} == {"small-model"}


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
        p = load_prompt("value-resolver")
    assert (p.source, p.text, p.version) == ("langfuse", "served v3", 3)
    args, kwargs = fake.get_prompt.call_args
    assert args == ("deep-agent/value-resolver",)
    assert kwargs["label"] == "staging" and kwargs["fallback"] == local_prompt("value-resolver")


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
async def test_prompt_label_change_takes_effect_without_rebuild(scripted_model, langfuse_enabled):
    served = {"text": local_prompt("system")}
    fake = MagicMock(get_prompt=MagicMock(side_effect=lambda *a, **k: _served(served["text"])))
    model = scripted_model(AIMessage("one"), AIMessage("two"))
    with patch("agent.langfuse_client.langfuse_client", fake):
        agent = create_deep_agent(model=model)
        await agent.ainvoke({"messages": [("user", "a")]}, {"configurable": {"thread_id": "p1"}})
        served["text"] = "You are the NEW deep analyst prompt v4."
        await agent.ainvoke({"messages": [("user", "b")]}, {"configurable": {"thread_id": "p2"}})

    first, second = system_text(model.seen[0]), system_text(model.seen[1])
    assert first.startswith(local_prompt("system")[:80])
    assert second.startswith("You are the NEW deep analyst prompt v4.")
    assert "Skills System" in second  # harness / skills sections after our prompt are preserved
    assert local_prompt("system")[:80] not in second


def test_run_config_links_generations_to_the_prompt_version(langfuse_enabled):
    client = _served("x", version=7)
    with patch.object(observability, "_langfuse_handler", return_value=object()), \
         patch.object(prompt_registry, "load_prompt",
                      return_value=prompt_registry.LoadedPrompt("system", "x", "langfuse", 7, client)):
        config = observability.build_run_config("t", tracing=True)
    assert config["metadata"]["langfuse_prompt"] is client
    assert config["metadata"]["prompt_version"] == 7
    assert config["metadata"]["prompt_source"] == "langfuse"


def test_sync_script_pushes_every_prompt_with_label():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "sync_deep_agent_prompts.py"
    spec = importlib.util.spec_from_file_location("sync_prompts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = MagicMock()
    pushed = module.sync(fake, label="staging", dry_run=False)
    assert pushed == [f"deep-agent/{k}" for k in PROMPT_KEYS]
    for call, key in zip(fake.create_prompt.call_args_list, PROMPT_KEYS):
        assert call.kwargs["name"] == f"deep-agent/{key}"
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
