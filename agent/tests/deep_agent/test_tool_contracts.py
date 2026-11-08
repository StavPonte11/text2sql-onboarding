import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tiktoken
from core.trino import TrinoExecutionResult

import agent.utils.jeen_metadata_client as jmc
from agent.utils.jeen_metadata_client import JeenMetadataClient, JeenMetadataError
from deep_agent import tools
from deep_agent.tools import DEEP_AGENT_TOOLS
from deep_agent.tools import enrichment as enrichment_tools
from deep_agent.tools import location as location_tools
from deep_agent.tools import metadata as metadata_tools
from deep_agent.tools import sql as sql_tools

ALL_TOOLS = DEEP_AGENT_TOOLS

# Minimal valid arguments for every tool (each must honour the envelope).
TOOL_ARGS = {
    "search_column_values": {"search_term": "תל אביב"},
    "resolve_location_to_wkt": {"location_name": "חיפה"},
    "execute_trino_query": {"sql": "SELECT 1"},
    "get_sample_rows": {"table_name": "t"},
    "get_column_distinct_values": {"table_name": "t", "column_name": "c"},
    "validate_sql": {"sql": "SELECT 1"},
    "enrich_sql_filters": {"sql": "SELECT 1 FROM t WHERE a = 'x'"},
}


def _client(fail: bool) -> MagicMock:
    client = MagicMock()
    names = ["get_catalog_prompt", "get_tables", "get_columns", "get_relationships", "get_table_profile",
             "search", "search_business_terms", "search_knowledge_pairs", "search_column_values"]
    for name in names:
        if fail:
            setattr(client, name, AsyncMock(side_effect=JeenMetadataError(f"{name} exploded")))
        else:
            setattr(client, name, AsyncMock(return_value={"values": [{"value": "v"}]} if name == "search_column_values"
                                            else "# catalog" if name == "get_catalog_prompt" else [{"x": 1}]))
    return client


@pytest.fixture
def patched(request):
    fail = request.param
    trino_res = (TrinoExecutionResult(success=False, error_message="boom", error_name="GENERIC_INTERNAL_ERROR",
                                      error_type="INTERNAL_ERROR")
                 if fail else TrinoExecutionResult(success=True, rows=[[1]], columns=["a"], row_count=1))
    enrich = (AsyncMock(side_effect=RuntimeError("llm down")) if fail
              else AsyncMock(return_value=("SELECT 1 FROM t WHERE a = 'X'", SimpleNamespace(enrichment_details=[]), True)))
    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=_client(fail)), \
         patch.object(enrichment_tools, "get_jeen_metadata_client", return_value=_client(fail)), \
         patch.object(sql_tools, "execute_query_sync", return_value=trino_res), \
         patch.object(location_tools, "get_geojson_polygon", return_value=None if fail else {"type": "Polygon"}), \
         patch.object(location_tools, "geojson_to_simplified_wkt", return_value="'POLYGON((0 0, 1 0, 1 1, 0 0))'"), \
         patch("agent.nodes.refiner.parse_jeen_catalog_tables", return_value=("s", ["t"])), \
         patch("agent.services.enrichment_orchestrator.EnrichmentOrchestrator.enrich_query", enrich):
        yield fail


def test_every_tool_is_covered():
    assert {t.name for t in ALL_TOOLS} == set(TOOL_ARGS)


def test_registered_tool_set():
    """The catalog is loaded for every run (CatalogMiddleware), so there is no catalog tool, and the
    tools whose information the catalog already contains are not offered to the model."""
    assert {t.name for t in DEEP_AGENT_TOOLS} == {
        "search_column_values", "get_column_distinct_values", "resolve_location_to_wkt",
        "execute_trino_query", "get_sample_rows", "validate_sql", "enrich_sql_filters",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True], ids=["success", "failure"], indirect=True)
@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
async def test_tool_returns_envelope(tool, patched):
    raw = await tool.ainvoke(TOOL_ARGS[tool.name])
    out = json.loads(raw)
    assert set(out) == {"ok", "data", "error"}, raw
    assert out["ok"] is (not patched), raw
    if patched:
        assert out["data"] is None
        assert {"name", "message"} <= set(out["error"])
        assert "hint" not in out["error"] and "category" not in out["error"]
    else:
        assert out["error"] is None
    assert "\n  " not in raw, "envelope must be compact (no indentation)"


@pytest.mark.asyncio
async def test_metadata_failure_says_data_is_not_empty():
    client = MagicMock(search_column_values=AsyncMock(side_effect=JeenMetadataError("auth expired")))
    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=client):
        out = json.loads(await tools.search_column_values.ainvoke({"search_term": "x"}))
    assert out["error"]["name"] == "METADATA_ERROR"
    assert "auth expired" in out["error"]["message"]
    assert "does not mean the data is empty" in out["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_is_error_flag_raises():
    result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="connection_id 999 not found")])
    session = MagicMock()
    session.initialize = AsyncMock()
    session.call_tool = AsyncMock(return_value=result)
    session_cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
    transport_cm = MagicMock(__aenter__=AsyncMock(return_value=(None, None, None)), __aexit__=AsyncMock(return_value=False))
    with patch.object(jmc, "streamablehttp_client", return_value=transport_cm), \
         patch.object(jmc, "ClientSession", return_value=session_cm):
        with pytest.raises(JeenMetadataError, match="connection_id 999 not found"):
            await jmc._call_mcp_tool("http://x", "k", "get_relationships", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_tables", "get_columns", "get_relationships"])
async def test_unexpected_payload_is_an_error_not_empty(method):
    client = JeenMetadataClient()
    with patch.object(client, "_call", AsyncMock(return_value="Error: auth expired")):
        with pytest.raises(JeenMetadataError, match="auth expired"):
            await getattr(client, method)()


async def _search(values):
    client = MagicMock(search_column_values=AsyncMock(return_value={"values": values}))
    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=client):
        return json.loads(await tools.search_column_values.ainvoke({"search_term": "x"}))


@pytest.mark.asyncio
async def test_search_values_handles_nulls_and_numbers():
    out = await _search([
        {"value": 12345, "table_name": "t", "column_name": "unit_id", "keyword_similarity": None, "semantic_distance": None},
        {"value": None, "keyword_similarity": "n/a"},
        "bare-string",
    ])
    assert out["ok"] is True
    m = out["data"]["matches"]
    assert m[0] == {"value": 12345, "table": "t", "column": "unit_id", "keyword_similarity": None, "semantic_distance": None}
    assert m[1]["value"] is None
    assert m[2] == {"value": "bare-string"}


@pytest.mark.asyncio
async def test_search_values_returns_every_match_without_prefix_guessing():
    values = [f"תל אביב {i}" for i in range(15)]
    out = await _search([{"value": v, "keyword_similarity": 0.9, "semantic_distance": 0.1} for v in values])
    assert [m["value"] for m in out["data"]["matches"]] == values
    assert "prefix_pattern" not in out["data"]


@pytest.mark.asyncio
async def test_search_values_says_when_the_limit_was_hit():
    full = await _search([{"value": f"v{i}"} for i in range(10)])  # default limit 10
    assert full["data"]["limit_reached"] is True and "higher limit" in full["data"]["notice"]
    fewer = await _search([{"value": "v1"}, {"value": "v2"}])
    assert fewer["data"]["limit_reached"] is False and fewer["data"]["notice"] is None


@pytest.mark.asyncio
async def test_search_values_no_match_points_at_unindexed_column_types():
    notice = (await _search([]))["data"]["notice"]
    assert all(t in notice for t in ("`id`", "`continuous`", "`time`", "`free_text`", "doesn't exist"))


@pytest.mark.asyncio
async def test_search_values_passes_limit_through_unclamped():
    client = MagicMock(search_column_values=AsyncMock(return_value={"values": []}))
    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=client):
        await tools.search_column_values.ainvoke({"search_term": "x", "limit": 500})
    assert client.search_column_values.call_args.kwargs["limit"] == 500


@pytest.mark.asyncio
async def test_location_returns_the_polygon_once():
    with patch.object(location_tools, "get_geojson_polygon", return_value={"type": "Polygon"}), \
         patch.object(location_tools, "geojson_to_simplified_wkt", return_value="'POLYGON((0 0, 1 0, 1 1, 0 0))'"):
        out = json.loads(tools.resolve_location_to_wkt.invoke({"location_name": "חיפה"}))
    assert out["data"] == {"location": "חיפה", "wkt": "POLYGON((0 0, 1 0, 1 1, 0 0))"}


def _old_search_column_values_output(search_term: str, res: dict) -> str:
    """The pre-#45 output format (summary + indent=2 full payload), reproduced for comparison."""
    lines = [f"TOP MATCHES for '{search_term}' (use these exact strings in SQL):"]
    for v in res["values"][:5]:
        lines.append(f"  - '{v['value']}'  (table: {v['table_name']}, column: {v['column_name']}, "
                     f"keyword_sim: {round(v['keyword_similarity'], 3)}, sem_dist: {round(v['semantic_distance'], 3)})")
    return "\n".join(lines) + "\n\nFull payload:\n" + json.dumps(res, indent=2, ensure_ascii=False)


@pytest.mark.asyncio
async def test_search_values_output_is_at_least_40_percent_smaller():
    res = {"values": [
        {"value": f"תל אביב {i}", "table_name": "city_info_table", "column_name": "city_name",
         "keyword_similarity": 0.912345 - i / 100, "semantic_distance": 0.123456 + i / 100,
         "semantic_type": "large_categorical", "connection_id": 89, "embedding_model": "nomic-embed-text"}
        for i in range(10)
    ]}
    client = MagicMock(search_column_values=AsyncMock(return_value=res))
    with patch.object(metadata_tools, "get_jeen_metadata_client", return_value=client):
        new = await tools.search_column_values.ainvoke({"search_term": "תל אביב"})
    enc = tiktoken.get_encoding("o200k_base")
    old_tokens = len(enc.encode(_old_search_column_values_output("תל אביב", res)))
    new_tokens = len(enc.encode(new))
    assert new_tokens <= 0.6 * old_tokens, (old_tokens, new_tokens)
