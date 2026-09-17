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
