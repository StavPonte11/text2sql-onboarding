import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import agent.utils.jeen_metadata_client as jmc
import core.trino as core_trino
from agent.config import settings
from agent.utils.jeen_metadata_client import JeenMetadataClient, JeenMetadataError, close_mcp_sessions
from core.trino import TrinoExecutionResult
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.tool_filter import EXCLUDED_BUILTIN_TOOLS

OK = TrinoExecutionResult(success=True, rows=[[7]], columns=["n"], row_count=1, query_id="q")


@pytest.mark.asyncio
async def test_unused_builtin_tools_are_not_offered_to_the_model(scripted_model):
    model = scripted_model(AIMessage("done"))
    await create_deep_agent(model=model).ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "t"}})
    offered = set(model.bound_tool_names[-1])
    assert not offered & EXCLUDED_BUILTIN_TOOLS
    assert {"read_file", "write_todos", "execute_trino_query", "submit_answer"} <= offered


# ── metadata cache ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    c = JeenMetadataClient()
    c.clear_cache()
    return c


@pytest.mark.asyncio
async def test_metadata_reads_are_cached(client):
    with patch.object(client, "_call_uncached", AsyncMock(return_value=[{"name": "t"}])) as fetch:
        first = await client.get_tables(connection_id=1)
        first.append({"name": "mutated"})                     # callers can't corrupt the cache
        second = await client.get_tables(connection_id=1)
        await client.get_tables(connection_id=2)               # different connection -> separate entry
    assert second == [{"name": "t"}]
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_identical_reads_are_single_flight(client):
    async def slow(*_):
        await asyncio.sleep(0.05)
        return [{"name": "t"}]

    with patch.object(client, "_call_uncached", AsyncMock(side_effect=slow)) as fetch:
        results = await asyncio.gather(*(client.get_relationships(connection_id=1) for _ in range(5)))
    assert fetch.await_count == 1
    assert all(r == [{"name": "t"}] for r in results)


@pytest.mark.asyncio
async def test_errors_are_not_cached_and_ttl_zero_disables(client, monkeypatch):
    with patch.object(client, "_call_uncached", AsyncMock(side_effect=[JeenMetadataError("boom"), [{"x": 1}]])) as fetch:
        with pytest.raises(JeenMetadataError):
            await client.get_tables(connection_id=1)
        assert await client.get_tables(connection_id=1) == [{"x": 1}]
    assert fetch.await_count == 2

    monkeypatch.setattr(settings, "JEEN_METADATA_CACHE_TTL_S", 0)
    with patch.object(client, "_call_uncached", AsyncMock(return_value=[])) as fetch:
        await client.get_tables(connection_id=1)
        await client.get_tables(connection_id=1)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_search_results_are_not_cached(client):
    with patch.object(client, "_call_uncached", AsyncMock(return_value={"values": []})) as fetch:
        await client.search_column_values("x", raw=True)
        await client.search_column_values("x", raw=True)
    assert fetch.await_count == 2


# ── persistent MCP session ──────────────────────────────────────────────────

class FakeTransport:
    """Counts MCP handshakes; call_tool can be told to fail once like a dropped session."""

    def __init__(self, fail_first_call=False):
        self.handshakes = 0
        self.calls = 0
        self.fail_first_call = fail_first_call

    def transport(self, *args, **kwargs):
        return MagicMock(__aenter__=AsyncMock(return_value=(None, None, None)), __aexit__=AsyncMock(return_value=False))

    def session(self, *args, **kwargs):
        outer = self

        async def initialize():
            outer.handshakes += 1

        async def call_tool(name, arguments, **kwargs):
            outer.calls += 1
            await asyncio.sleep(0.01)
            if outer.fail_first_call and outer.calls == 1:
                raise ConnectionError("session expired")
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text='{"tables": []}')])

        session = MagicMock(initialize=initialize, call_tool=call_tool)
        return MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first_call, expected_handshakes", [(False, 1), (True, 2)])
async def test_mcp_session_is_reused_across_calls(fail_first_call, expected_handshakes):
    fake = FakeTransport(fail_first_call)
    with patch.object(jmc, "streamablehttp_client", side_effect=fake.transport), \
         patch.object(jmc, "ClientSession", side_effect=fake.session):
        for _ in range(3):
            await jmc._call_mcp_tool("http://mcp", "k", "get_tables", {})
        await asyncio.gather(*(jmc._call_mcp_tool("http://mcp", "k", "get_tables", {}) for _ in range(5)))
        await close_mcp_sessions()
    assert fake.handshakes == expected_handshakes
    assert fake.calls == 8 + int(fail_first_call)


@pytest.mark.asyncio
async def test_mcp_per_call_sessions_when_reuse_disabled(monkeypatch):
    monkeypatch.setattr(settings, "JEEN_METADATA_REUSE_SESSION", False)
    fake = FakeTransport()
    with patch.object(jmc, "streamablehttp_client", side_effect=fake.transport), \
         patch.object(jmc, "ClientSession", side_effect=fake.session):
        for _ in range(3):
            await jmc._call_mcp_tool("http://mcp", "k", "get_tables", {})
    assert fake.handshakes == 3


# ── Trino connection reuse ──────────────────────────────────────────────────

def _fake_connection(fail=False):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.description = [("n",)]
    cur.fetchall.return_value = [(1,)]
    cur.query_id = "20261020_000000_00001_abcde"
    if fail:
        cur.execute.side_effect = RuntimeError("connection reset")
    return conn


def test_trino_connection_is_reused_per_thread_and_dropped_on_error(monkeypatch):
    monkeypatch.setattr(core_trino.settings, "TRINO_ENABLED", True)
    core_trino._drop_reusable_connection()
    conns = [_fake_connection(), _fake_connection(fail=True), _fake_connection()]
    with patch.object(core_trino, "get_trino_connection", side_effect=conns) as connect:
        for _ in range(3):
            assert core_trino.execute_query_sync("SELECT 1", reuse_connection=True).success
        assert connect.call_count == 1                       # one connection for three queries
        conns[0].cursor.return_value.execute.side_effect = RuntimeError("connection reset")
        assert not core_trino.execute_query_sync("SELECT 1", reuse_connection=True).success
        assert core_trino.execute_query_sync("SELECT 1", reuse_connection=True).success is False  # conns[1] fails
        assert core_trino.execute_query_sync("SELECT 1", reuse_connection=True).success      # fresh conns[2]
    assert connect.call_count == 3
    core_trino._drop_reusable_connection()


def test_default_execution_still_opens_and_closes_a_connection(monkeypatch):
    monkeypatch.setattr(core_trino.settings, "TRINO_ENABLED", True)
    conn = _fake_connection()
    with patch.object(core_trino, "get_trino_connection", return_value=conn):
        core_trino.execute_query_sync("SELECT 1")
    conn.close.assert_called_once()
