"""
Wiring: the main agent delegates to location-analyst, which extracts, resolves, inspects the schema
and submits a LocationPlan; the finalised plan reaches the parent, and the parent's SQL with the
geometry placeholder executes with the real WKT substituted (and is shown substituted).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.services import geo_utils
from core.trino import TrinoExecutionResult
from deep_agent import answer as answer_module
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext
from deep_agent.subagents import SUBAGENT_TOOLS, build_subagents
from tests.deep_agent.conftest import system_text
from tests.deep_agent.geo.fixtures import CITY

QUESTION = "כמה אירועים היו בתוך גבולות באר שבע"
COLUMNS = [{"name": "city", "type": "varchar", "semanticType": "categorical"},
           {"name": "lon", "type": "double"}, {"name": "lat", "type": "double"}]


def _role(messages) -> str:
    text = system_text(messages)
    for role in ("location-extractor", "location-analyst"):
        if f"You are {role}" in text:
            return role
    return "main"


EXTRACTION = AIMessage("", tool_calls=[{"name": "LocationExtraction", "id": "e1", "args": {
    "candidates": [{"mention": "באר שבע", "is_geographic": True, "reason": "city whose boundary bounds the events"}],
    "locations": [{"original_mention": "באר שבע", "canonical_hebrew": "באר שבע", "english_name": "beer_sheva",
                   "entity_type": "city", "normalization_notes": []}]}}])


def _tool_payloads(messages, name):
    return [json.loads(m.text) for m in messages if m.type == "tool" and m.name == name]


def policy(messages):
    tool_msgs = [m for m in messages if m.type == "tool"]
    if _role(messages) == "location-extractor":
        return EXTRACTION
    if _role(messages) == "main":
        task = [m for m in tool_msgs if m.name == "task"]
        if not task:
            return AIMessage("", tool_calls=[{"name": "task", "id": "t1", "args": {
                "subagent_type": "location-analyst", "description": f"{QUESTION} | table events"}}])
        executed = [m for m in tool_msgs if m.name == "execute_trino_query"]
        if not executed:
            plan = json.loads(task[0].text)
            predicate = plan["usages"][0]["predicate_sql"]
            return AIMessage("", tool_calls=[{"name": "execute_trino_query", "id": "x1", "args": {
                "sql": f"SELECT count(*) AS n FROM events WHERE {predicate}"}}])
        sql = json.loads(task[0].text)["usages"][0]["predicate_sql"]
        return AIMessage("", tool_calls=[{"name": "DeepAgentAnswer", "id": "a1", "args": {
            "sql": f"SELECT count(*) AS n FROM events WHERE {sql}", "explanation_he": "ספירה", "confidence": "high"}}])

    # location-analyst: extract -> inspect schema -> resolve area -> plan
    if not tool_msgs:
        return AIMessage("", tool_calls=[{"name": "extract_locations", "id": "s1", "args": {"query": QUESTION}}])
    if len(tool_msgs) == 1:
        return AIMessage("", tool_calls=[{"name": "get_table_columns", "id": "s2", "args": {"table_name": "events"}}])
    if len(tool_msgs) == 2:
        loc = _tool_payloads(messages, "extract_locations")[0]["data"]["locations"][0]
        return AIMessage("", tool_calls=[{"name": "resolve_location", "id": "s3", "args": {
            "canonical_hebrew": loc["canonical_hebrew"], "geometry": "area", "entity_type": loc["entity_type"],
            "english_name": loc["english_name"]}}])
    ref = _tool_payloads(messages, "resolve_location")[0]["data"]["feature_ref"]
    return AIMessage("", tool_calls=[{"name": "LocationPlan", "id": "s4", "args": {"usages": [{
        "original_mention": "באר שבע", "canonical_hebrew": "באר שבע", "english_name": "beer_sheva",
        "intent": "inside the municipal boundary", "operation": "within_area",
        "target": {"table": "events", "kind": "lon_lat", "lon_column": "lon", "lat_column": "lat"},
        "feature_ref": ref, "evidence": ["events has lon/lat columns"]}]}}])


@pytest.mark.asyncio
async def test_location_analyst_end_to_end(policy_model):
    model = policy_model(policy)
    client = MagicMock(get_columns=AsyncMock(return_value=COLUMNS))
    trino = TrinoExecutionResult(success=True, rows=[[42]], columns=["n"], row_count=1, query_id="q-geo")
    geo_utils.nominatim_search.cache_clear()
    with patch.object(tools, "get_jeen_metadata_client", return_value=client), \
         patch.object(geo_utils, "_nominatim_search", side_effect=lambda q, polygon, limit: (CITY,)) as geocode, \
         patch.object(tools, "execute_query_sync", return_value=trino) as execute, \
         patch.object(answer_module, "execute_query_sync") as reexecute:
        result = await create_deep_agent(model=model).ainvoke(
            {"messages": [("user", QUESTION)]}, {"configurable": {"thread_id": "geo-e2e"}},
            context=DeepAgentContext(connection_id=1))

    # Stage 1 ran the extractor model on the question verbatim, with its own prompt.
    extractor_calls = [msgs for msgs in model.seen if _role(msgs) == "location-extractor"]
    assert len(extractor_calls) == 1 and extractor_calls[0][-1].content == QUESTION

    # Stage 2 called the geocoder once, asking for a polygon (area needed).
    assert geocode.call_count == 1 and geocode.call_args.args[1] is True

    # The finalised plan (not the model's raw output) reached the parent, without geometry inline.
    task = next(m for m in result["messages"] if m.type == "tool" and m.name == "task")
    plan = json.loads(task.text)
    u = plan["usages"][0]
    assert u["status"] == "ready" and u["geometry_type"] == "Polygon"
    assert u["predicate_sql"].startswith("ST_Contains(ST_GeometryFromText('@beer_sheva_") and "POLYGON" not in task.text

    # The registry travelled back to the parent, the executor substituted it, the answer matched it.
    assert result["geo_registry"]["features"]
    executed_sql = execute.call_args.args[0]
    assert "'POLYGON ((" in executed_sql and "@" not in executed_sql
    reexecute.assert_not_called()
    final = result["final_result"]
    assert final["status"] == "matched" and final["result"]["rows"] == [[42]]
    assert "POLYGON" in final["sql"] and "_wkt@" not in final["sql"]

    # location-analyst was offered exactly its tools (+ resolve_location, the plan tool and harness tools).
    sub_call = next(i for i, msgs in enumerate(model.seen) if _role(msgs) == "location-analyst")
    offered = set(model.bound_tool_names[sub_call])
    expected = {t.name for t in SUBAGENT_TOOLS["location-analyst"]} | {"extract_locations", "resolve_location",
                                                                        "LocationPlan"}
    assert expected <= offered and "execute_trino_query" not in offered and "task" not in offered


@pytest.mark.asyncio
async def test_unknown_placeholder_is_rejected_by_the_executor():
    out = json.loads(tools.execute_trino_query.invoke({"sql": "SELECT ST_GeometryFromText('@ghost_wkt@')"}))
    assert out["ok"] is False and out["error"]["category"] == "INVALID_INPUT"
    out = json.loads(tools.validate_sql_syntax.invoke({"sql": "SELECT ST_GeometryFromText('@ghost_wkt@')"}))
    assert out["error"]["category"] == "INVALID_INPUT"


def test_location_analyst_spec_and_gateway_geocoder_injection():
    gateway = MagicMock()
    gateway.name = "get_geocode_address"
    gateway.args_schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    reverse = MagicMock()
    reverse.name = "get_location_by_coordinates"
    specs = {s["name"]: s for s in build_subagents(MagicMock(), {"location-analyst": [gateway, reverse]})}
    spec = specs["location-analyst"]
    names = [t.name for t in spec["tools"]]
    assert {"extract_locations", "resolve_location", "get_location_by_coordinates"} <= set(names)
    assert "get_geocode_address" not in names  # consumed by resolve_location, not offered to the model
    assert "gateway:get_geocode_address" in next(t for t in spec["tools"] if t.name == "resolve_location").description
    assert spec["response_format"].schema.__name__ == "LocationPlan"
    assert "resolve_location_to_wkt" not in {t.name for t in specs["value-resolver"]["tools"]}
