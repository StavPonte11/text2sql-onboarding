"""Model selection: the models LLM_BASE_URL serves, and a per-request model for every LLM call."""
import asyncio
import json
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END, START, StateGraph

from agent import llm as llm_module
from agent.llm import UnknownModelError, available_models, check_model, get_llm, selected_model


@pytest.fixture(autouse=True)
def served(monkeypatch):
    """LLM_BASE_URL serves two models; the /models call is counted."""
    monkeypatch.setattr(llm_module, "_models_cache", None)
    client = MagicMock()
    client.models.list.return_value = [SimpleNamespace(id="qwen"), SimpleNamespace(id="gpt-oss")]
    with patch("openai.OpenAI", return_value=client) as factory:
        yield factory, client


def test_available_models_come_from_the_endpoint_and_are_cached(served):
    factory, client = served
    assert available_models() == ["gpt-oss", "qwen"]
    assert available_models() == ["gpt-oss", "qwen"]
    assert client.models.list.call_count == 1                      # cached
    kwargs = factory.call_args.kwargs
    assert kwargs["base_url"] == llm_module.settings.LLM_BASE_URL and kwargs["api_key"] == llm_module.settings.LLM_API_KEY


def test_check_model_rejects_a_model_the_endpoint_does_not_serve():
    assert check_model("qwen") == "qwen"
    with pytest.raises(UnknownModelError, match="Available: gpt-oss, qwen"):
        check_model("llama")


def test_selected_model_overrides_flags_and_the_argument_overrides_both():
    flags = {"QUERY_BUILDER_MODEL": "from-flags"}
    assert get_llm("query_builder", runtime_flags=flags).model_name == "from-flags"
    with selected_model("qwen"):
        assert get_llm("query_builder", runtime_flags=flags).model_name == "qwen"
        assert get_llm("refiner").model_name == "qwen"              # every node, flags or not
        assert get_llm(model="gpt-oss").model_name == "gpt-oss"
    assert get_llm("query_builder", runtime_flags=flags).model_name == "from-flags"  # reset after the block


@pytest.mark.asyncio
async def test_selected_model_reaches_llm_calls_inside_langgraph_nodes():
    class S(TypedDict, total=False):
        model: str

    async def node(state):
        await asyncio.sleep(0)
        return {"model": get_llm("query_builder").model_name}

    graph = StateGraph(S)
    graph.add_node("n", node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    with selected_model("qwen"):
        out = await graph.compile().ainvoke({})
    assert out["model"] == "qwen"


@pytest.mark.asyncio
async def test_list_models_tool():
    from agent import mcp_server

    out = json.loads(await mcp_server.list_models())
    assert out == {"models": ["gpt-oss", "qwen"], "default": llm_module.settings.LLM_MODEL}


@pytest.mark.asyncio
async def test_list_models_tool_reports_an_unreachable_endpoint(served):
    from agent import mcp_server

    served[1].models.list.side_effect = RuntimeError("connection refused")
    out = json.loads(await mcp_server.list_models())
    assert out["models"] == [] and "connection refused" in out["list_error"]


@pytest.mark.asyncio
async def test_structured_tool_rejects_an_unknown_model_before_running():
    from agent import mcp_server

    with patch.object(mcp_server.agent_graph, "ainvoke") as graph:
        out = json.loads(await mcp_server.chat_with_agent(connection_id=89, query="q", model="llama"))
    assert "not available" in out["error"]
    graph.assert_not_called()


@pytest.mark.asyncio
async def test_deep_tool_passes_the_model_to_the_deep_agent():
    from agent import mcp_server

    with patch("deep_agent.service.run_deep_chat", AsyncMock(return_value={"status": "completed"})) as run:
        await mcp_server.deep_agent(connection_id=89, query="q", model="qwen")
    assert run.await_args.kwargs["model"] == "qwen"


@pytest.mark.asyncio
async def test_deep_service_builds_one_agent_per_model_with_that_model_as_reviewer():
    from deep_agent import service

    built = []

    def fake_create(**kwargs):
        built.append(kwargs)
        return object()

    service.reset_for_tests()
    qwen_llm = object()
    with patch("deep_agent.agent.create_deep_agent", fake_create), \
         patch("agent.llm.get_llm", MagicMock(return_value=qwen_llm)) as get, \
         patch.object(service, "build_checkpointer", AsyncMock(return_value=object())):
        default = await service.get_deep_agent()
        qwen = await service.get_deep_agent("qwen")
        assert await service.get_deep_agent("qwen") is qwen         # cached per model
    service.reset_for_tests()
    assert default is not qwen and len(built) == 2
    assert "model" not in built[0]                                   # default: the configured LLM
    get.assert_called_once_with(model="qwen")
    assert built[1]["model"] is qwen_llm and built[1]["validator_model"] is qwen_llm  # agent and reviewer
