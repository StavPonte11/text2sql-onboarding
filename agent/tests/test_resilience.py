import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import json

from agent.nodes.schema_explorer import schema_explorer_node
from agent.nodes.finalizer import finalizer_node
from core.models.models import Table
from agent.state import AgentState

@pytest.mark.asyncio
async def test_tts_g1_01_jeen_catalog_fetch(mock_langfuse, mock_redis):
    state: AgentState = {
        "user_query": "test query",
    }
    
    mock_jeen = MagicMock()
    mock_jeen.is_configured = True
    mock_jeen.get_catalog_prompt = AsyncMock(return_value="# Jeen Catalog\n\"postgres\".\"public\".\"users\"")

    with patch("agent.nodes.schema_explorer.get_jeen_metadata_client", return_value=mock_jeen):
        result = await schema_explorer_node(state)
        
        assert result.get("jeen_catalog") == "# Jeen Catalog\n\"postgres\".\"public\".\"users\""
        assert result.get("execution_path") == ["schema_explorer"]

@pytest.mark.asyncio
async def test_tts_g1_07_esca_resilient_fallback_and_finalizer(mock_langfuse, mock_llm):
    # Simulate state after refiner fails Esca write (so esca_write_failed=True)
    state: AgentState = {
        "user_query": "test query",
        "scoping_mode": "hybrid",
        "messages": [],
        "query_enrichments": [],
        "schema_plan": "",
        "sql_query": "SELECT 1",
        "trino_error": None,
        "refinement_count": 0,
        "raw_data_ref": None, # Should be None because esca write failed
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "last_error": None,
        "hallucinated_tables": None,
        "esca_write_failed": True, # The key indicator
        "inline_result_rows": [{"col1": "val1"}, {"col1": "val2"}], # Fallback rows
        "error_history": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0
    }
    
    with patch("agent.nodes.finalizer.get_esca_client") as mock_get_esca:
        # It shouldn't even call esca client if esca_write_failed is True
        result = await finalizer_node(state)
        
        # Verify it falls back to inline_result_rows
        # In finalizer, the LLM will be given the inline rows
        assert mock_get_esca.called == False
        
        # Verify finalizer updates the summary based on mock LLM
        assert "summary" in result
        assert result["summary"] != state["summary"], "Summary should be updated by finalizer_node"
        assert result["summary"] != ""
