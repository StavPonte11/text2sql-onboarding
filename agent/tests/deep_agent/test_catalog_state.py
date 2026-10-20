from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage

from deep_agent import tools
from deep_agent.agent import create_deep_agent

CATALOG = '# Jeen Catalog\n"postgres"."public"."flights_table"\n| launch_country | varchar | categorical |'
QUESTION = "כמה טיסות המריאו מצרפת"
DRAFT = "SELECT COUNT(*) FROM \"postgres\".\"public\".\"flights_table\" WHERE launch_country = 'צרפת'"


class Marker(AsyncCallbackHandler):
    """A caller-supplied callback handler; it must reach the enrichment LLM calls."""


def _policy(first_tool: str):
    def policy(messages):
        tool_msgs = [m for m in messages if m.type == "tool"]
        steps = [first_tool, "enrich_sql_filters", "enrich_sql_filters"] if first_tool == "enrich_sql_filters" \
            else ["get_database_catalog", "enrich_sql_filters"]
        if len(tool_msgs) < len(steps):
            name = steps[len(tool_msgs)]
            args = {"sql": DRAFT} if name == "enrich_sql_filters" else {}
            return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"t{len(tool_msgs)}"}])
        return AIMessage("done")
    return policy


@pytest.fixture
def enrichment():
    plan = SimpleNamespace(enrichment_details=[SimpleNamespace(
        changed_filter=True, column="launch_country", original_value="צרפת", refined_values=["France"],
        old_operator="=", new_operator="=",
    )])
    enrich = AsyncMock(return_value=(DRAFT.replace("'צרפת'", "'France'"), plan, True))
    parse = MagicMock(return_value=({"flights_table": {"launch_country": "varchar"}}, ["flights_table"]))
    client = MagicMock(get_catalog_prompt=AsyncMock(return_value=CATALOG))
    with patch("agent.services.enrichment_orchestrator.EnrichmentOrchestrator.enrich_query", enrich), \
         patch("agent.nodes.refiner.parse_jeen_catalog_tables", parse), \
         patch.object(tools, "get_jeen_metadata_client", return_value=client):
        yield SimpleNamespace(enrich=enrich, parse=parse, client=client)


@pytest.mark.asyncio
async def test_enrichment_uses_cached_catalog_question_and_parent_config(policy_model, enrichment):
    agent = create_deep_agent(model=policy_model(_policy("get_database_catalog")))
    marker = Marker()
    result = await agent.ainvoke(
        {"messages": [("user", QUESTION)]},
        {"configurable": {"thread_id": "e1"}, "callbacks": [marker]},
    )

    assert result["jeen_catalog"] == CATALOG
    enrichment.client.get_catalog_prompt.assert_awaited_once()          # fetched once, by the catalog tool
    enrichment.parse.assert_called_once_with(CATALOG)                   # exact cached text, not a model re-typing

    kwargs = enrichment.enrich.await_args.kwargs
    assert kwargs["user_request"] == QUESTION
    assert kwargs["initial_sql"] == DRAFT
    config = kwargs["config"]
    assert config is not None
    handlers = getattr(config.get("callbacks"), "handlers", config.get("callbacks")) or []
    assert marker in handlers, "caller callbacks (Langfuse) must propagate into enrichment"

    enrich_msg = next(m for m in result["messages"] if m.type == "tool" and m.name == "enrich_sql_filters")
    assert "'France'" in enrich_msg.text


@pytest.mark.asyncio
async def test_enrichment_before_catalog_fetches_once_and_caches(policy_model, enrichment):
    agent = create_deep_agent(model=policy_model(_policy("enrich_sql_filters")))
    result = await agent.ainvoke({"messages": [("user", QUESTION)]}, {"configurable": {"thread_id": "e2"}})

    assert result["jeen_catalog"] == CATALOG
    assert enrichment.enrich.await_count == 3
    enrichment.client.get_catalog_prompt.assert_awaited_once()  # later calls hit the state cache


@pytest.mark.asyncio
async def test_catalog_fetch_failure_is_reported(policy_model, enrichment):
    enrichment.client.get_catalog_prompt.side_effect = RuntimeError("mcp down")
    out = await tools.enrich_sql_filters.ainvoke({"sql": DRAFT})
    assert '"category":"METADATA_ERROR"' in out
    enrichment.enrich.assert_not_awaited()


def test_model_only_supplies_sql():
    props = tools.enrich_sql_filters.tool_call_schema.model_json_schema()["properties"]
    assert list(props) == ["sql"]
