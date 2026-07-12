import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import List

from agent.services.enrichment_models import SQLFilterParams, AgentSQLTable
from agent.services.hybrid_searcher import (
    find_table_id,
    get_query_embedding,
    query_db_semantic,
    query_db_exact,
    query_db_trigram,
    query_db_digits_match,
    reciprocal_rank_fusion,
    rerank_candidates,
    search_workflow,
    unit_id_workflow,
    HybridSearcher
)

# Test resolving table IDs through mock DB session
def test_find_table_id_qualified(mocker):
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_table_row = MagicMock()
    mock_table_row.id = "table-uuid-123"
    
    mock_session.exec.return_value.first.return_value = mock_table_row
    mocker.patch("agent.services.hybrid_searcher.Session", return_value=mock_session)
    
    # 1. Test three-part catalog.schema.table name
    table_id = find_table_id("catalog.schema.table")
    assert table_id == "table-uuid-123"
    
    # 2. Test two-part schema.table name
    table_id_2 = find_table_id("schema.table")
    assert table_id_2 == "table-uuid-123"
    
    # 3. Test single part table name
    table_id_3 = find_table_id("table")
    assert table_id_3 == "table-uuid-123"

# Test calling the query embedding client wrapper
def test_get_query_embedding(mocker):
    # Mock successful call
    mocker.patch("agent.services.hybrid_searcher.get_embedding", return_value=[0.1, 0.2, 0.3])
    emb = get_query_embedding("active")
    assert emb == [0.1, 0.2, 0.3]
    
    # Mock failed call returning None
    mocker.patch("agent.services.hybrid_searcher.get_embedding", return_value=None)
    emb_fail = get_query_embedding("inactive")
    assert emb_fail is None

# Test semantic database raw query execution
def test_query_db_semantic(mocker):
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.execute.return_value.fetchall.return_value = [("ACTIVE",), ("COMPLETED",)]
    mocker.patch("agent.services.hybrid_searcher.Session", return_value=mock_session)
    
    res = query_db_semantic("tbl-id", "status", [0.1, 0.2, 0.3])
    assert res == ["ACTIVE", "COMPLETED"]

# Test exact database raw query execution
def test_query_db_exact(mocker):
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.execute.return_value.fetchall.return_value = [("ACTIVE",)]
    mocker.patch("agent.services.hybrid_searcher.Session", return_value=mock_session)
    
    res = query_db_exact("tbl-id", "status", "active")
    assert res == ["ACTIVE"]

# Test trigram database raw query execution
def test_query_db_trigram(mocker):
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.execute.return_value.fetchall.return_value = [("ACTIVE",)]
    mocker.patch("agent.services.hybrid_searcher.Session", return_value=mock_session)
    
    res = query_db_trigram("tbl-id", "status", "act")
    assert res == ["ACTIVE"]

# Test RRF formula scoring logic
def test_reciprocal_rank_fusion():
    sem_list = ["A", "B"]
    lex_list = ["B", "C"]
    
    # Expected scores:
    # A: 1 / (60 + 1) = 1/61 ~ 0.01639
    # B: 1 / (60 + 2) [semantic] + 1 / (60 + 1) [lexical] = 1/62 + 1/61 ~ 0.03252
    # C: 1 / (60 + 2) = 1/62 ~ 0.01612
    # Sorted order should be: B, A, C
    merged = reciprocal_rank_fusion(sem_list, lex_list, k=60)
    assert merged == ["B", "A", "C"]

# Test Fast-Path exact match short-circuit
@pytest.mark.asyncio
async def test_search_workflow_fast_path(mocker):
    # Mock exact match to return value
    mocker.patch("agent.services.hybrid_searcher.query_db_exact", return_value=["EXACT_MATCH"])
    mock_embed = mocker.patch("agent.services.hybrid_searcher.get_query_embedding")
    
    res = await search_workflow("tbl-id", "status", "exact_value")
    assert res == ["EXACT_MATCH"]
    
    # Embedder was NEVER called because we returned early
    mock_embed.assert_not_called()

# Test concurrent search workflow merging using RRF
@pytest.mark.asyncio
async def test_search_workflow_rrf(mocker):
    mocker.patch("agent.services.hybrid_searcher.query_db_exact", return_value=[])
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1, 0.2])
    
    # Mock semantic and trigram lexical databases
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", return_value=["A", "B"])
    mocker.patch("agent.services.hybrid_searcher.query_db_trigram", return_value=["B", "C"])
    
    res = await search_workflow("tbl-id", "status", "pattern", use_rrf=True)
    # Expected merged rank order sorted descending by RRF scores is B, A, C
    assert res == ["B", "A", "C"]

# Test large_unit_id workflow pipeline (numbers regex, filter out mismatch semantic candidate)
@pytest.mark.asyncio
async def test_unit_id_workflow(mocker):
    # Mock exact numeric digit matching and semantic vector retrieval
    mocker.patch("agent.services.hybrid_searcher.query_db_digits_match", return_value=["st 52", "warehouse 521"])
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1, 0.2])
    # Semantic has st 52 (contains 52) and offices 99 (does not contain 52)
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", return_value=["st 52", "offices 99"])
    
    res = await unit_id_workflow("tbl-id", "place", "st 52")
    
    # Verify result list includes st 52 and warehouse 521, but "offices 99" is filtered out (does not contain 52)
    assert "st 52" in res
    assert "warehouse 521" in res
    assert "offices 99" not in res

@pytest.mark.asyncio
async def test_unit_id_workflow_soft_fallback(mocker):
    # No exact numeric matches in database
    mocker.patch("agent.services.hybrid_searcher.query_db_digits_match", return_value=[])
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1, 0.2])
    
    # Semantic query only retrieves "Aisle five" (does not contain the literal "5")
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", return_value=["Aisle five"])
    
    res = await unit_id_workflow("tbl-id", "place", "Aisle 5")
    
    # Because there are no numeric matches anywhere, it should fallback to "Aisle five" instead of returning []
    assert res == ["Aisle five"]

# Test outer routing in HybridSearcher.search
@pytest.mark.asyncio
async def test_hybrid_searcher_routing(mocker):
    filters = [
        SQLFilterParams(
            source_table="dataverse.orders",
            source_column="order_status",
            operator="=",
            value="active",
            original_expression="order_status = 'active'",
            match_type="exact"
        ),
        SQLFilterParams(
            source_table="dataverse.orders",
            source_column="place_id",
            operator="=",
            value="st 52",
            original_expression="place_id = 'st 52'",
            match_type="exact"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="dataverse.orders",
            columns={
                "order_status": {"column_type": "large_category"},
                "place_id": {"column_type": "large_unit_id"}
            }
        )
    ]
    
    mocker.patch("agent.services.hybrid_searcher.find_table_id", return_value="tbl-orders-id")
    mock_workflow_cat = mocker.patch("agent.services.hybrid_searcher.search_workflow", new_callable=AsyncMock, return_value=["ACTIVE"])
    mock_workflow_unit = mocker.patch("agent.services.hybrid_searcher.unit_id_workflow", new_callable=AsyncMock, return_value=["st 52"])
    
    results = await HybridSearcher.search(filters, tables)
    
    # Verify routing hit corresponding category vs unit ID functions
    mock_workflow_cat.assert_called_once_with("tbl-orders-id", "order_status", "active")
    mock_workflow_unit.assert_called_once_with("tbl-orders-id", "place_id", "st 52")
    
    assert results["order_status#@#active"] == ["ACTIVE"]
    assert results["place_id#@#st 52"] == ["st 52"]

@pytest.mark.asyncio
async def test_hybrid_searcher_like_operator_stripping(mocker):
    filters = [
        SQLFilterParams(
            source_table="dataverse.tickets",
            source_column="priority",
            operator="LIKE",
            value="%high%",
            original_expression="priority LIKE '%high%'",
            match_type="substring"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="dataverse.tickets",
            columns={"priority": {"column_type": "large_category"}}
        )
    ]
    
    mocker.patch("agent.services.hybrid_searcher.find_table_id", return_value="tbl-tickets-id")
    mock_workflow = mocker.patch("agent.services.hybrid_searcher.search_workflow", new_callable=AsyncMock)
    mock_workflow.return_value = ["HIGH_PRIORITY", "MEDIUM_HIGH"]
    
    results = await HybridSearcher.search(filters, tables)
    
    mock_workflow.assert_called_once_with("tbl-tickets-id", "priority", "high")
    assert "priority#@#%high%" in results
    assert results["priority#@#%high%"] == ["HIGH_PRIORITY", "MEDIUM_HIGH"]

@pytest.mark.asyncio
async def test_hybrid_searcher_in_list_expansion(mocker):
    filters = [
        SQLFilterParams(
            source_table="dataverse.users",
            source_column="role",
            operator="IN",
            value=["admin", "editor"],
            original_expression="role IN ('admin', 'editor')",
            match_type="in_list"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="dataverse.users",
            columns={"role": {"column_type": "large_category"}}
        )
    ]
    
    mocker.patch("agent.services.hybrid_searcher.find_table_id", return_value="tbl-users-id")
    
    async def mock_search_workflow_side_effect(table_id, col_name, value):
        if value == "admin":
            return ["SUPER_ADMIN", "ADMIN"]
        return ["CONTENT_EDITOR"]
        
    mocker.patch("agent.services.hybrid_searcher.search_workflow", side_effect=mock_search_workflow_side_effect)
    
    results = await HybridSearcher.search(filters, tables)
    
    assert "role#@#admin" in results
    assert results["role#@#admin"] == ["SUPER_ADMIN", "ADMIN"]
    assert "role#@#editor" in results
    assert results["role#@#editor"] == ["CONTENT_EDITOR"]

@pytest.mark.asyncio
async def test_search_workflow_embedding_failure(mocker):
    mocker.patch("agent.services.hybrid_searcher.query_db_exact", return_value=[])
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=None)
    
    mock_semantic = mocker.patch("agent.services.hybrid_searcher.query_db_semantic")
    mock_trigram = mocker.patch("agent.services.hybrid_searcher.query_db_trigram", return_value=["LEXICAL_MATCH"])
    
    res = await search_workflow("tbl-id", "status", "failed_embed_pattern")
    assert res == ["LEXICAL_MATCH"]
    
    mock_semantic.assert_not_called()
    mock_trigram.assert_called_once_with("tbl-id", "status", "failed_embed_pattern")

@pytest.mark.asyncio
async def test_hybrid_searcher_unresolved_table(mocker):
    filters = [
        SQLFilterParams(
            source_table="dataverse.ghost_table",
            source_column="status",
            operator="=",
            value="active",
            original_expression="status = 'active'",
            match_type="exact"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="dataverse.ghost_table",
            columns={"status": {"column_type": "large_category"}}
        )
    ]
    
    mocker.patch("agent.services.hybrid_searcher.find_table_id", return_value=None)
    mock_workflow = mocker.patch("agent.services.hybrid_searcher.search_workflow", new_callable=AsyncMock)
    
    results = await HybridSearcher.search(filters, tables)
    assert results == {}
    mock_workflow.assert_not_called()

@pytest.mark.asyncio
async def test_hybrid_searcher_caching_logic(mocker):
    filters = [
        SQLFilterParams(
            source_table="dataverse.sales", source_column="region",
            operator="=", value="na", original_expression="", match_type="exact"
        ),
        SQLFilterParams(
            source_table="dataverse.sales", source_column="region",
            operator="=", value="na", original_expression="", match_type="exact"
        ),
        SQLFilterParams(
            source_table="dataverse.sales", source_column="status",
            operator="=", value="open", original_expression="", match_type="exact"
        )
    ]
    
    tables = [
        AgentSQLTable(
            name="dataverse.sales",
            columns={
                "region": {"column_type": "large_category"},
                "status": {"column_type": "large_category"}
            }
        )
    ]
    
    mock_find_table = mocker.patch("agent.services.hybrid_searcher.find_table_id", return_value="tbl-sales-id")
    mock_workflow = mocker.patch("agent.services.hybrid_searcher.search_workflow", new_callable=AsyncMock)
    mock_workflow.side_effect = [["NORTH_AMERICA"], ["OPEN_STATUS"]]
    
    results = await HybridSearcher.search(filters, tables)
    
    assert len(results) == 2
    assert results["region#@#na"] == ["NORTH_AMERICA"]
    assert results["status#@#open"] == ["OPEN_STATUS"]
    mock_find_table.assert_called_once_with("dataverse.sales")
    assert mock_workflow.call_count == 2

@pytest.mark.asyncio
async def test_unit_id_workflow_multiple_digits(mocker):
    mock_db_digits = mocker.patch("agent.services.hybrid_searcher.query_db_digits_match", return_value=["Aisle 5, Rack 12", "Aisle 5, Rack 12B"])
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1])
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", return_value=[
        "Aisle 5, Rack 12",
        "Aisle 5, Rack 9"
    ])
    
    res = await unit_id_workflow("tbl-id", "location", "Aisle 5 Rack 12")
    
    mock_db_digits.assert_called_once_with("tbl-id", "location", ["5", "12"])
    assert "Aisle 5, Rack 12" in res
    assert "Aisle 5, Rack 12B" in res
    assert "Aisle 5, Rack 9" not in res

@pytest.mark.asyncio
async def test_unit_id_workflow_no_digits(mocker):
    mock_db_digits = mocker.patch("agent.services.hybrid_searcher.query_db_digits_match")
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1])
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", return_value=["HQ", "Main Office"])
    
    res = await unit_id_workflow("tbl-id", "location", "Headquarters")
    
    mock_db_digits.assert_not_called()
    assert res == ["HQ", "Main Office"]

@pytest.mark.asyncio
async def test_workflow_exception_handling(mocker):
    mocker.patch("agent.services.hybrid_searcher.get_query_embedding", return_value=[0.1])
    mocker.patch("agent.services.hybrid_searcher.query_db_exact", return_value=[])
    mocker.patch("agent.services.hybrid_searcher.query_db_semantic", side_effect=Exception("Database Connection Dropped!"))
    mocker.patch("agent.services.hybrid_searcher.query_db_trigram", return_value=["LEX_1"])
    
    res = await search_workflow("tbl-id", "status", "test_crash")
    assert "LEX_1" in res

def test_reciprocal_rank_fusion_edge_cases():
    merged_1 = reciprocal_rank_fusion([], ["A", "B"])
    assert merged_1 == ["A", "B"]
    
    merged_2 = reciprocal_rank_fusion(["Z"], [])
    assert merged_2 == ["Z"]
    
    merged_3 = reciprocal_rank_fusion(["A", "B"], ["Y", "Z"])
    assert set(merged_3) == {"A", "Y", "B", "Z"}

def test_rerank_candidates():
    candidates = ["Candidate 1", "Candidate 2", "Candidate 3", "Candidate 4", "Candidate 5", "Candidate 6", "Candidate 7"]
    top_5 = rerank_candidates("query", candidates)
    assert len(top_5) == 5
    assert "Candidate 6" not in top_5

@pytest.mark.asyncio
async def test_hybrid_searcher_skips_unmatched_types(mocker):
    filters = [
        SQLFilterParams(source_table="db.tbl", source_column="amount", operator="=", value="100", original_expression="", match_type="exact"),
        SQLFilterParams(source_table="db.tbl", source_column="is_active", operator="=", value="True", original_expression="", match_type="exact")
    ]
    tables = [
        AgentSQLTable(
            name="db.tbl",
            columns={
                "amount": {"column_type": "numeric"},
                "is_active": {"column_type": "boolean"}
            }
        )
    ]
    mock_find = mocker.patch("agent.services.hybrid_searcher.find_table_id")
    results = await HybridSearcher.search(filters, tables)
    assert results == {}
    mock_find.assert_not_called()
