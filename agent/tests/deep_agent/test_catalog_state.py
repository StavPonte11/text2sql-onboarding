from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage

from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.tools import enrichment as enrichment_tools

CATALOG = '# Jeen Catalog\n"postgres"."public"."flights_table"\n| launch_country | varchar | categorical |'
QUESTION = "כמה טיסות המריאו מצרפת"
DRAFT = "SELECT COUNT(*) FROM \"postgres\".\"public\".\"flights_table\" WHERE launch_country = 'צרפת'"


class Marker(AsyncCallbackHandler):
    """A caller-supplied callback handler; it must reach the enrichment LLM calls."""


def _policy(enrich_calls: int):
    def policy(messages):
        tool_msgs = [m for m in messages if m.type == "tool"]
        if len(tool_msgs) < enrich_calls:
            return AIMessage("", tool_calls=[{"name": "enrich_sql_filters", "args": {"sql": DRAFT}, "id": f"t{len(tool_msgs)}"}])
        return AIMessage("done")
    return policy


@pytest.fixture
def enrichment(fake_catalog):
    plan = SimpleNamespace(enrichment_details=[SimpleNamespace(
        changed_filter=True, column="launch_country", original_value="צרפת", refined_values=["France"],
        old_operator="=", new_operator="=",
    )])
    enrich = AsyncMock(return_value=(DRAFT.replace("'צרפת'", "'France'"), plan, True))
    parse = MagicMock(return_value=({"flights_table": {"launch_country": "varchar"}}, ["flights_table"]))
    client = MagicMock(get_catalog_prompt=AsyncMock(return_value=CATALOG))
    with patch("agent.services.enrichment_orchestrator.EnrichmentOrchestrator.enrich_query", enrich), \
         patch("agent.nodes.refiner.parse_jeen_catalog_tables", parse), \
         patch.object(enrichment_tools, "get_jeen_metadata_client", return_value=client):
        fake_catalog.get_catalog_prompt.return_value = CATALOG  # what CatalogMiddleware loads for the run
        yield SimpleNamespace(enrich=enrich, parse=parse, client=client, run_catalog=fake_catalog)


@pytest.mark.asyncio
async def test_enrichment_uses_the_run_catalog_question_and_parent_config(policy_model, enrichment):
    agent = create_deep_agent(model=policy_model(_policy(1)))
    marker = Marker()
    result = await agent.ainvoke(
        {"messages": [("user", QUESTION)]},
        {"configurable": {"thread_id": "e1"}, "callbacks": [marker]},
    )

    assert result["jeen_catalog"] == CATALOG
    enrichment.run_catalog.get_catalog_prompt.assert_awaited_once()     # loaded once, by CatalogMiddleware
    enrichment.client.get_catalog_prompt.assert_not_awaited()           # the tool never fetches it itself
    enrichment.parse.assert_called_once_with(CATALOG)                   # exact loaded text, not a model re-typing

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
async def test_repeated_enrichment_never_refetches_the_catalog(policy_model, enrichment):
    agent = create_deep_agent(model=policy_model(_policy(3)))
    await agent.ainvoke({"messages": [("user", QUESTION)]}, {"configurable": {"thread_id": "e2"}})

    assert enrichment.enrich.await_count == 3
    enrichment.run_catalog.get_catalog_prompt.assert_awaited_once()
    enrichment.client.get_catalog_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_fetch_failure_is_reported(policy_model, enrichment):
    enrichment.client.get_catalog_prompt.side_effect = RuntimeError("mcp down")
    out = await tools.enrich_sql_filters.ainvoke({"sql": DRAFT})
    assert '"name":"METADATA_ERROR"' in out
    enrichment.enrich.assert_not_awaited()


def test_model_only_supplies_sql():
    props = tools.enrich_sql_filters.tool_call_schema.model_json_schema()["properties"]
    assert list(props) == ["sql"]


@pytest.mark.asyncio
async def test_enrichment_after_clarification_gets_the_question_and_the_reply(policy_model, enrichment):
    """The reply to a clarifying question arrives as a new user message; it must not replace the question."""
    def policy(messages):
        humans = [m for m in messages if m.type == "human"]
        if len(humans) == 1:
            return AIMessage("לאיזה מחוז הכוונה?")                     # clarifying question, no tools
        if messages[-1].type == "human":
            return AIMessage("", tool_calls=[{"name": "enrich_sql_filters", "args": {"sql": DRAFT}, "id": "e"}])
        return AIMessage("done")

    agent = create_deep_agent(model=policy_model(policy))
    config = {"configurable": {"thread_id": "clarify"}}
    await agent.ainvoke({"messages": [("user", "כמה טיסות המריאו מהמחוז הצפוני")]}, config)
    await agent.ainvoke({"messages": [("user", "חיפה")]}, config)

    assert enrichment.enrich.await_args.kwargs["user_request"] == "כמה טיסות המריאו מהמחוז הצפוני\nחיפה"
