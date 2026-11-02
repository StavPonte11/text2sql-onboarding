import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import deep_agent.context as context_module
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext, date_line
from deep_agent.tools import metadata as metadata_tools
from tests.deep_agent.conftest import system_text

CATALOGS = {1: "# catalog for connection 1", 2: "# catalog for connection 2"}


def _search_then_answer_policy(messages):
    """Thread-local behaviour: one value search, then finish."""
    if not [m for m in messages if m.type == "tool"]:
        return AIMessage("", tool_calls=[{"name": "search_column_values", "args": {"search_term": "Acme"}, "id": "s1"}])
    return AIMessage("done")


def _catalog_by_connection():
    async def catalog(connection_id=None):
        await asyncio.sleep(0.01)  # force interleaving between the two requests
        return CATALOGS.get(connection_id, "# default connection catalog")
    return AsyncMock(side_effect=catalog)


def _value_client():
    async def search(search_term, table=None, column=None, limit=10, raw=False, connection_id=None):
        await asyncio.sleep(0.01)
        return {"values": [{"value": f"{search_term} @ {connection_id}"}]}
    return MagicMock(search_column_values=AsyncMock(side_effect=search))


@pytest.mark.asyncio
async def test_concurrent_requests_for_different_connections_are_isolated(policy_model, fake_catalog):
    model = policy_model(_search_then_answer_policy)
    agent = create_deep_agent(model=model)
    fake_catalog.get_catalog_prompt = _catalog_by_connection()
    client = _value_client()

    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=client):
        r1, r2 = await asyncio.gather(
            agent.ainvoke({"messages": [("user", "q1")]}, {"configurable": {"thread_id": "conn-1"}},
                          context=DeepAgentContext(connection_id=1)),
            agent.ainvoke({"messages": [("user", "q2")]}, {"configurable": {"thread_id": "conn-2"}},
                          context=DeepAgentContext(connection_id=2)),
        )

    # Each run loaded its own connection's catalog into its own state...
    assert sorted(c.kwargs["connection_id"] for c in fake_catalog.get_catalog_prompt.await_args_list) == [1, 2]
    assert (r1["jeen_catalog"], r2["jeen_catalog"]) == (CATALOGS[1], CATALOGS[2])
    # ...each model call saw only its own catalog...
    prompts = [system_text(seen) for seen in model.seen]
    for prompt in prompts:
        assert (CATALOGS[1] in prompt) != (CATALOGS[2] in prompt)
    # ...and tools called the metadata service with their own connection id.
    assert sorted(c.kwargs["connection_id"] for c in client.search_column_values.await_args_list) == [1, 2]
    v1 = json.loads(next(m for m in r1["messages"] if m.type == "tool").text)
    v2 = json.loads(next(m for m in r2["messages"] if m.type == "tool").text)
    assert v1["data"]["matches"][0]["value"] == "Acme @ 1" and v2["data"]["matches"][0]["value"] == "Acme @ 2"


@pytest.mark.asyncio
async def test_no_context_falls_back_to_default_connection(policy_model, fake_catalog):
    agent = create_deep_agent(model=policy_model(lambda messages: AIMessage("done")))
    await agent.ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "default"}})
    assert fake_catalog.get_catalog_prompt.await_args.kwargs["connection_id"] is None  # client uses its configured default


class _FrozenDatetime(datetime):
    instant = datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


def test_date_is_computed_in_jerusalem_time(monkeypatch):
    monkeypatch.setattr(context_module, "datetime", _FrozenDatetime)
    _FrozenDatetime.instant = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)  # 00:30 on Oct 21 in Israel (UTC+3)
    assert date_line("Asia/Jerusalem").startswith("Today's date: 2026-10-21 (Wednesday")
    assert date_line("UTC").startswith("Today's date: 2026-10-20")


@pytest.mark.asyncio
async def test_date_is_injected_per_model_call_not_at_construction(scripted_model, monkeypatch):
    monkeypatch.setattr(context_module, "datetime", _FrozenDatetime)
    model = scripted_model(AIMessage("first"), AIMessage("first"), AIMessage("second"), AIMessage("second"))  # + reminder replies
    agent = create_deep_agent(model=model)  # built "yesterday"

    _FrozenDatetime.instant = datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc)  # 23:30 Oct 20 in Israel
    await agent.ainvoke({"messages": [("user", "a")]}, {"configurable": {"thread_id": "d1"}})
    _FrozenDatetime.instant = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)  # 00:30 Oct 21 in Israel
    await agent.ainvoke({"messages": [("user", "b")]}, {"configurable": {"thread_id": "d2"}})

    def last_human(messages):
        return next(m for m in reversed(messages) if m.type == "human").text

    assert last_human(model.seen[0]).startswith("[Today's date: 2026-10-20")
    assert last_human(model.seen[2]).startswith("[Today's date: 2026-10-21")
    assert last_human(model.seen[2]).endswith("b")
    # The system prompt is byte-identical across days, so provider prompt caching keeps working.
    assert system_text(model.seen[0]) == system_text(model.seen[2])
    assert "Today's date" not in system_text(model.seen[1])


@pytest.mark.asyncio
async def test_date_prefix_is_not_persisted_to_state(scripted_model):
    agent = create_deep_agent(model=scripted_model(AIMessage("ok"), AIMessage("ok")))
    result = await agent.ainvoke({"messages": [("user", "שאלה")]}, {"configurable": {"thread_id": "p1"}})
    assert result["messages"][0].text == "שאלה"
