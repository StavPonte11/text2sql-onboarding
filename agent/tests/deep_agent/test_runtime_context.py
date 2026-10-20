import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import deep_agent.context as context_module
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext, date_line
from tests.deep_agent.conftest import system_text

NO_PAIRS_CATALOG = "# catalog for connection 1\n(no knowledge pairs)"
FULL_CATALOG = "# catalog for connection 2\nknowledge pairs: 3"


def _catalog_then_pairs_policy(messages):
    """Thread-local behaviour: fetch catalog, then search knowledge pairs, then answer."""
    tool_msgs = [m for m in messages if m.type == "tool"]
    if not tool_msgs:
        return AIMessage("", tool_calls=[{"name": "get_database_catalog", "args": {}, "id": "c1"}])
    if tool_msgs[-1].name == "get_database_catalog":
        return AIMessage("", tool_calls=[{"name": "search_knowledge_pairs", "args": {"query": "כמה טיסות"}, "id": "k1"}])
    return AIMessage("done")


def _metadata_client():
    async def catalog(connection_id=None):
        await asyncio.sleep(0.01)  # force interleaving between the two requests
        return NO_PAIRS_CATALOG if connection_id == 1 else FULL_CATALOG

    async def pairs(query, limit=10, connection_id=None):
        await asyncio.sleep(0.01)
        return [{"question": query, "connection": connection_id}]

    return MagicMock(get_catalog_prompt=AsyncMock(side_effect=catalog),
                     search_knowledge_pairs=AsyncMock(side_effect=pairs))


@pytest.mark.asyncio
async def test_concurrent_requests_for_different_connections_are_isolated(policy_model):
    agent = create_deep_agent(model=policy_model(_catalog_then_pairs_policy))
    client = _metadata_client()

    with patch.object(tools, "get_jeen_metadata_client", return_value=client):
        r1, r2 = await asyncio.gather(
            agent.ainvoke({"messages": [("user", "q1")]}, {"configurable": {"thread_id": "conn-1"}},
                          context=DeepAgentContext(connection_id=1)),
            agent.ainvoke({"messages": [("user", "q2")]}, {"configurable": {"thread_id": "conn-2"}},
                          context=DeepAgentContext(connection_id=2)),
        )

    # Each request fetched its own catalog...
    assert sorted(c.kwargs["connection_id"] for c in client.get_catalog_prompt.await_args_list) == [1, 2]
    # ...capabilities landed in each thread's own state...
    assert r1["connection_capabilities"]["knowledge_pairs"] is False
    assert r2["connection_capabilities"]["knowledge_pairs"] is True
    # ...so only connection 2 actually searched knowledge pairs, with its own connection id.
    client.search_knowledge_pairs.assert_awaited_once()
    assert client.search_knowledge_pairs.await_args.kwargs["connection_id"] == 2

    pairs_1 = json.loads(next(m for m in r1["messages"] if m.type == "tool" and m.name == "search_knowledge_pairs").text)
    pairs_2 = json.loads(next(m for m in r2["messages"] if m.type == "tool" and m.name == "search_knowledge_pairs").text)
    assert pairs_1["data"]["pairs"] == [] and "NO knowledge pairs" in pairs_1["data"]["notice"]
    assert pairs_2["data"] == [{"question": "כמה טיסות", "connection": 2}]


@pytest.mark.asyncio
async def test_no_context_falls_back_to_default_connection(policy_model):
    agent = create_deep_agent(model=policy_model(_catalog_then_pairs_policy))
    client = _metadata_client()
    with patch.object(tools, "get_jeen_metadata_client", return_value=client):
        await agent.ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "default"}})
    assert client.get_catalog_prompt.await_args.kwargs["connection_id"] is None  # client uses its configured default


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
    model = scripted_model(AIMessage("first"), AIMessage("second"))
    agent = create_deep_agent(model=model)  # built "yesterday"

    _FrozenDatetime.instant = datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc)  # 23:30 Oct 20 in Israel
    await agent.ainvoke({"messages": [("user", "a")]}, {"configurable": {"thread_id": "d1"}})
    _FrozenDatetime.instant = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)  # 00:30 Oct 21 in Israel
    await agent.ainvoke({"messages": [("user", "b")]}, {"configurable": {"thread_id": "d2"}})

    def last_human(messages):
        return next(m for m in reversed(messages) if m.type == "human").text

    assert last_human(model.seen[0]).startswith("[Today's date: 2026-10-20")
    assert last_human(model.seen[1]).startswith("[Today's date: 2026-10-21")
    assert last_human(model.seen[1]).endswith("b")
    # The system prompt is byte-identical across days, so provider prompt caching keeps working.
    assert system_text(model.seen[0]) == system_text(model.seen[1])
    assert "Today's date" not in system_text(model.seen[1])


@pytest.mark.asyncio
async def test_date_prefix_is_not_persisted_to_state(scripted_model):
    agent = create_deep_agent(model=scripted_model(AIMessage("ok")))
    result = await agent.ainvoke({"messages": [("user", "שאלה")]}, {"configurable": {"thread_id": "p1"}})
    assert result["messages"][0].text == "שאלה"
