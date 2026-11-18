import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import List

from agent.services.enrichment_models import SQLFilterParams, AgentSQLTable
from agent.services.hybrid_searcher import HybridSearcher

@pytest.fixture
def mock_mcp_client(mocker):
    mock_client = AsyncMock()
    mocker.patch("agent.services.hybrid_searcher.get_jeen_metadata_client", return_value=mock_client)
    return mock_client

@pytest.mark.asyncio
async def test_hybrid_searcher_search(mock_mcp_client):
    mock_mcp_client.search_column_values.side_effect = [
        ["Tel Aviv"],  # First search for 'Tel'
        ["USA"]        # Second search for 'US'
    ]
    
    filters = [
        SQLFilterParams(
            source_table="users",
            source_column="city",
            operator="=",
            value="Tel",
            original_expression="users.city = 'Tel'",
            match_type="exact"
        ),
        SQLFilterParams(
            source_table="users",
            source_column="country",
            operator="=",
            value="US",
            original_expression="users.country = 'US'",
            match_type="exact"
        )
    ]
    
    table = AgentSQLTable(
        name="users",
        columns={
            "city": {"semantic_type": "large_categorical"},
            "country": {"semantic_type": "large_categorical"}
        }
    )
    
    results = await HybridSearcher.search(filters, [table])
    
    from agent.config import settings
    d = settings.CACHE_KEY_DELIMITER
    
    assert f"city{d}Tel" in results
    assert f"country{d}US" in results
    assert results[f"city{d}Tel"] == ["Tel Aviv"]
    assert results[f"country{d}US"] == ["USA"]
    
    assert mock_mcp_client.search_column_values.call_count == 2


# ── categorical columns: checked against the catalog's complete value list, never searched ──

POLICIES = ["No-Fly Zone below 15k feet", "Unrestricted Flying Allowed", "Fully Closed Airspace"]
DISTRICTS_A = ["Central District", "Haifa District"]
DISTRICTS_B = ["District-1", "District-2"]


def _filter(table, column, operator, value):
    return SQLFilterParams(source_table=table, source_column=column, operator=operator, value=value,
                           original_expression="", match_type="exact")


def _tables():
    return [
        AgentSQLTable(name='"postgres"."public"."policies"', columns={
            "flight_policy": {"semantic_type": "categorical", "values_all": POLICIES},
            "note": {"semantic_type": "categorical", "values_all": None},
        }),
        AgentSQLTable(name='"postgres"."public"."flights"', columns={
            "district": {"semantic_type": "categorical", "values_all": DISTRICTS_A}}),
        AgentSQLTable(name='"postgres"."public"."landings"', columns={
            "district": {"semantic_type": "categorical", "values_all": DISTRICTS_B}}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["postgres.public.policies", '"postgres"."public"."policies"'])
async def test_value_outside_a_complete_list_gets_the_list_as_candidates(mock_mcp_client, table):
    from agent.config import settings
    d = settings.CACHE_KEY_DELIMITER
    results = await HybridSearcher.search([_filter(table, "flight_policy", "=", "Fully Closed")], _tables())
    assert results == {f"flight_policy{d}Fully Closed": POLICIES}
    mock_mcp_client.search_column_values.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operator, value", [
    ("=", "Fully Closed Airspace"),
    ("IN", ["Fully Closed Airspace", "Unrestricted Flying Allowed"]),
    ("LIKE", "%Closed%"),
    ("!=", "anything"),
])
async def test_values_the_list_allows_are_left_alone(mock_mcp_client, operator, value):
    results = await HybridSearcher.search([_filter("postgres.public.policies", "flight_policy", operator, value)], _tables())
    assert results == {}
    mock_mcp_client.search_column_values.assert_not_called()


@pytest.mark.asyncio
async def test_only_the_unlisted_values_of_an_in_list_are_flagged(mock_mcp_client):
    from agent.config import settings
    d = settings.CACHE_KEY_DELIMITER
    f = _filter("postgres.public.policies", "flight_policy", "IN", ["Fully Closed Airspace", "No-Fly"])
    assert await HybridSearcher.search([f], _tables()) == {f"flight_policy{d}No-Fly": POLICIES}


@pytest.mark.asyncio
async def test_like_pattern_matching_nothing_is_flagged(mock_mcp_client):
    from agent.config import settings
    d = settings.CACHE_KEY_DELIMITER
    f = _filter("postgres.public.policies", "flight_policy", "LIKE", "%Open%")
    assert await HybridSearcher.search([f], _tables()) == {f"flight_policy{d}%Open%": POLICIES}


@pytest.mark.asyncio
async def test_each_table_uses_its_own_list_for_a_shared_column_name(mock_mcp_client):
    from agent.config import settings
    d = settings.CACHE_KEY_DELIMITER
    ok = await HybridSearcher.search([_filter("postgres.public.landings", "district", "=", "District-1")], _tables())
    bad = await HybridSearcher.search([_filter("postgres.public.flights", "district", "=", "District-1")], _tables())
    assert ok == {} and bad == {f"district{d}District-1": DISTRICTS_A}
    # unknown table and the column name is in two tables: no guessing which list applies
    assert await HybridSearcher.search([_filter("", "district", "=", "Nowhere")], _tables()) == {}


@pytest.mark.asyncio
async def test_columns_without_a_complete_list_are_not_checked(mock_mcp_client):
    assert await HybridSearcher.search([_filter("postgres.public.policies", "note", "=", "x")], _tables()) == {}


def test_catalog_parsing_keeps_complete_value_lists_only():
    from agent.nodes.refiner import parse_jeen_catalog_tables

    catalog = '''"postgres"."public"."policies": Policies
  - "flight_policy" (varchar(255)) [semantic_type=categorical] [distinct_count=3]: Policy — values (all): 'No-Fly Zone below 15k feet', 'Fully Closed Airspace', 'Pilot''s Choice'
  - "note" (varchar) [semantic_type=categorical]: Notes — values (examples): 'a', 'b'
  - "shape" (varchar) [semantic_type=categorical]: Shape — values (all): 'POLYGON((34.8 32.0, 34.9 …'
'''
    _, tables = parse_jeen_catalog_tables(catalog)
    cols = tables[0].columns
    assert cols["flight_policy"]["values_all"] == ["No-Fly Zone below 15k feet", "Fully Closed Airspace", "Pilot's Choice"]
    assert cols["note"]["values_all"] is None      # examples are not the full list
    assert cols["shape"]["values_all"] is None     # truncated values can't be compared
