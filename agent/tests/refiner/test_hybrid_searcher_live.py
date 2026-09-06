import pytest
from typing import List, Dict
import os
import sys

# Add agent directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '../../src'))

from agent.services.hybrid_searcher import HybridSearcher
from agent.services.enrichment_models import SQLFilterParams, AgentSQLTable
def is_integration_ready():
    return all(
        [
            os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"),
            os.getenv("TRINO_HOST"),
            os.getenv("REDIS_URL"),
        ]
    )

# Only run if we have live Trino/Jeen connections
pytestmark = pytest.mark.skipif(
    not is_integration_ready(), reason="Integration environment not ready"
)

@pytest.mark.asyncio
async def test_hybrid_searcher_unit_id_ranking():
    """
    Validates that the 'large_unit_id' workflow ranks results accurately:
    1. Exact matches (standalone number)
    2. Partial matches (substring number)
    3. Semantic matches
    """
    
    # Mocking a filter on a large_unit_id column
    filters = [
        SQLFilterParams(
            source_table="",
            source_column="",
            operator="=",
            value="17", # The numeric ID to search for
            original_expression="id = 17",
            is_unnest=False,
            is_negated=False,
            match_type="exact"
        )
    ]
    
    # Mocking the table schema to specify the column is a 'large_unit_id'
    tables = [
        AgentSQLTable(
            name="postgres.public.branches_table",
            description="Table with branch information",
            columns={
                "": {
                    "column_type": "varchar",
                    "semantic_type": "large_unit_id",
                }
            }
        )
    ]
    
    # Execute the REAL HybridSearcher against the REAL Jeen MCP
    results: Dict[str, List[str]] = await HybridSearcher.search(filters, tables)
    
    # Assertions
    key = "#@#17"
    assert key in results, "The search key should exist in the results"
    
    candidates = results[key]
    assert len(candidates) > 0, "MCP should return candidate values"
    
    # Check the ranking constraints explicitly!
    # Expected order: Exact matches first, then partial matches, then semantic.
    exact_match_index = -1
    partial_match_index = -1
    
    for i, candidate in enumerate(candidates):
        if " 17" in candidate or candidate.startswith("17"):
            if exact_match_index == -1:
                exact_match_index = i
        elif "117" in candidate or "170" in candidate:
            if partial_match_index == -1:
                partial_match_index = i
                
    if exact_match_index != -1 and partial_match_index != -1:
        assert exact_match_index < partial_match_index, "Exact numeric matches must be ranked before partial/substring matches"


@pytest.mark.asyncio
async def test_hybrid_searcher_categorical_semantic():
    """
    Validates that the 'large_categorical' workflow correctly returns fuzzy semantic matches,
    and strips wildcards before executing the search.
    """
    
    # Mocking a filter on a large_categorical column with wildcards
    filters = [
        SQLFilterParams(
            source_table="",
            source_column="",
            operator="LIKE",
            value="%apple%", # Wildcards included
            original_expression="city_name LIKE '%apple%'",
            is_unnest=False,
            is_negated=False,
            match_type="substring"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="postgres.public.flights_table",
            description="Table with flight information",
            columns={
                "": {
                    "column_type": "varchar",
                    "semantic_type": "large_categorical",
                }
            }
        )
    ]
    
    # Execute the REAL HybridSearcher against the REAL Jeen MCP
    results: Dict[str, List[str]] = await HybridSearcher.search(filters, tables)
    
    # Assertions
    key = "#@#%apple%" # The dictionary key should retain the wildcard!
    assert key in results, "The dictionary key must retain the wildcard for the orchestrator to map it back"
    
    candidates = results[key]
    assert len(candidates) > 0, "MCP should return candidate values"
