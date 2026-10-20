import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agent.config import settings
from deep_agent import utils_mcp
from deep_agent.agent import acreate_deep_agent, create_deep_agent
from deep_agent.utils_mcp import UTILS_TOOL_ALLOCATION, allocate, gateway_connection, load_utils_tools, wrap_utils_tool

SCHEMA = {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}


def fake_tool(name: str, result=None, exc: Exception | None = None) -> StructuredTool:
    async def _run(**kwargs):
        if exc:
            raise exc
        return result if result is not None else json.dumps({"echo": kwargs, "tool": name})
    return StructuredTool.from_function(coroutine=_run, name=name, description=f"{name} description", args_schema=SCHEMA)


GATEWAY_TOOLS = [
    fake_tool("utils__datetime_parse_relative_expression"),   # namespaced by the gateway
    fake_tool("text_normalize"),
    fake_tool("trino_validate_spatial_expression"),
    fake_tool("geo_create_buffer"),
    fake_tool("geo_validate_geometry"),
    fake_tool("trino_run_query"),                              # not allow-listed: SQL stays on the guarded path
    fake_tool("langfuse_add_score"),                           # write tool, never exposed
]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "JARVIS_GATEWAY_URL", "http://jarvis-gateway:8001/")
    monkeypatch.setattr(settings, "JARVIS_API_KEY", "test-key")
    client = MagicMock()

    async def get_tools():
        return GATEWAY_TOOLS

    client.get_tools.side_effect = get_tools
    with patch("langchain_mcp_adapters.client.MultiServerMCPClient", return_value=client) as cls:
        yield cls


def test_gateway_connection_uses_namespace_and_api_key(gateway):
    conn = gateway_connection()
    assert conn["url"] == "http://jarvis-gateway:8001/mcp/global/"
    assert conn["transport"] == "streamable_http"
    assert conn["headers"] == {"Authorization": "ApiKey test-key"}


@pytest.mark.asyncio
async def test_disabled_without_gateway_url():
    with patch("langchain_mcp_adapters.client.MultiServerMCPClient") as cls:
        allocation = await load_utils_tools()
    cls.assert_not_called()
    assert all(tools == [] for tools in allocation.values())


@pytest.mark.asyncio
async def test_allocation_by_agent(gateway):
    allocation = await load_utils_tools()
    names = {agent: sorted(t.name for t in tools) for agent, tools in allocation.items()}
    assert names["main"] == sorted(UTILS_TOOL_ALLOCATION["main"])
    assert names["value-resolver"] == sorted(UTILS_TOOL_ALLOCATION["value-resolver"])
    assert names["result-verifier"] == [] and names["schema-scout"] == []
    every = {n for ns in names.values() for n in ns}
    assert "trino_run_query" not in every and "langfuse_add_score" not in every


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ConnectionError("gateway down"), "timeout"])
async def test_unreachable_gateway_degrades_gracefully(monkeypatch, failure, caplog):
    monkeypatch.setattr(settings, "JARVIS_GATEWAY_URL", "http://jarvis-gateway:8001")
    monkeypatch.setattr(settings, "UTILS_MCP_TIMEOUT_S", 0.05)
    client = MagicMock()

    async def get_tools():
        if failure == "timeout":
            await asyncio.sleep(1)
        raise failure

    client.get_tools.side_effect = get_tools
    with patch("langchain_mcp_adapters.client.MultiServerMCPClient", return_value=client):
        allocation = await load_utils_tools()
    assert all(tools == [] for tools in allocation.values())
    assert "continuing without them" in caplog.text


@pytest.mark.asyncio
async def test_wrapped_tool_returns_envelope():
    wrapped = wrap_utils_tool(fake_tool("x"), "text_normalize")
    out = json.loads(await wrapped.ainvoke({"expression": "ת״א"}))
    assert out["ok"] is True and out["data"]["echo"] == {"expression": "ת״א"}
    assert wrapped.name == "text_normalize"
    assert wrapped.description.startswith("[Utils-Server]")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [
    fake_tool("x", result=json.dumps({"error": "Unprocessable", "status_code": 422, "response_text": "cannot parse 'השבוע'"})),
    fake_tool("x", exc=RuntimeError("connection reset")),
])
async def test_wrapped_tool_errors_are_external_tool_errors(tool):
    out = json.loads(await wrap_utils_tool(tool, "datetime_parse_relative_expression").ainvoke({"expression": "השבוע"}))
    assert out["ok"] is False and out["error"]["category"] == "EXTERNAL_TOOL_ERROR"


@pytest.mark.asyncio
async def test_content_blocks_are_parsed():
    tool = fake_tool("x", result=[{"type": "text", "text": '{"date": "2026-10-17"}'}])
    out = json.loads(await wrap_utils_tool(tool, "datetime_parse_relative_expression").ainvoke({"expression": "לפני 3 ימים"}))
    assert out["data"] == {"date": "2026-10-17"}


def test_duplicate_gateway_names_are_deduplicated():
    allocation = allocate([fake_tool("text_normalize"), fake_tool("utils__text_normalize")])
    assert [t.name for t in allocation["main"]] == ["text_normalize"]


@pytest.mark.asyncio
async def test_agents_are_offered_their_utils_tools(gateway, scripted_model):
    model = scripted_model(AIMessage("done"))
    agent = await acreate_deep_agent(model=model)
    await agent.ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "u"}})
    offered = set(model.bound_tool_names[0])
    assert set(UTILS_TOOL_ALLOCATION["main"]) <= offered
    assert "geo_create_buffer" not in offered  # value-resolver only


def test_subagents_receive_their_share(gateway):
    allocation = asyncio.run(load_utils_tools())
    with patch("deep_agent.agent.create_harness_agent") as harness:
        create_deep_agent(model=MagicMock(), extra_tools=allocation)
    subagents = {s["name"]: {t.name for t in s["tools"]} for s in harness.call_args.kwargs["subagents"]}
    assert set(UTILS_TOOL_ALLOCATION["value-resolver"]) <= subagents["value-resolver"]
    assert not subagents["result-verifier"] & utils_mcp.ALLOWED_UTILS_TOOLS
