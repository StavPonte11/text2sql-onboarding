import pytest
from unittest.mock import patch, MagicMock

from agent.nodes.schema_explorer import hybrid_search_tables
from core.models.models import Table

def test_hybrid_search_tables_strict_mode():
    session_mock = MagicMock()
    
    table1 = Table(id="t1", name="table_1", schema_name="public", catalog="cat", status="production")
    table2 = Table(id="t2", name="table_2", schema_name="public", catalog="cat", status="production")
    table3 = Table(id="t3", name="table_3", schema_name="public", catalog="cat", status="deprecated")
    
    session_mock.exec.return_value.all.return_value = [table1, table2, table3]
    
    # Mock execute for vector search
    session_mock.execute.return_value.fetchall.return_value = [("t1",)]
    
    # Mock session.get for final return
    def mock_get(cls, id):
        return {"t1": table1, "t2": table2, "t3": table3}.get(id)
    session_mock.get.side_effect = mock_get
    
    # Mock enrichment version for keyword search
    session_mock.exec.return_value.first.return_value = None
    
    # Test strict mode allows ONLY t1
    results = hybrid_search_tables(
        query="table", 
        query_embedding=[0.0], 
        session=session_mock, 
        allowed_tables=["t1"], 
        allowed_statuses=["production"], 
        scoping_mode="strict"
    )
    
    assert len(results) == 1
    assert results[0].id == "t1"

    # Test hybrid mode allows t1 and t2 (because status="production")
    session_mock.execute.return_value.fetchall.return_value = [("t1",), ("t2",)]
    results_hybrid = hybrid_search_tables(
        query="table", 
        query_embedding=[0.0], 
        session=session_mock, 
        allowed_tables=["t1"], 
        allowed_statuses=["production"], 
        scoping_mode="hybrid"
    )
    
    assert len(results_hybrid) == 2
    ids = {r.id for r in results_hybrid}
    assert "t1" in ids
    assert "t2" in ids
