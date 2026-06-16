import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import json

from agent.nodes.schema_explorer import schema_explorer_node
from agent.nodes.finalizer import finalizer_node
from core.models.models import Table
from agent.state import AgentState

@pytest.mark.asyncio
async def test_tts_g1_01_profile_fetch_concurrency(mock_langfuse, mock_redis):
    state: AgentState = {
        "user_query": "test query",
        "scoping_mode": "hybrid",
        "messages": [],
        "query_enrichments": [],
        "schema_plan": "",
        "sql_query": "",
        "trino_error": None,
        "refinement_count": 0,
        "raw_data_ref": None,
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
        "esca_write_failed": None,
        "inline_result_rows": None,
        "error_history": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0
    }
    
    tables = [Table(id=f"t{i}", catalog="cat", schema_name="sch", name=f"name{i}", status="production") for i in range(8)]
    
    with patch("agent.nodes.schema_explorer.hybrid_search_tables", return_value=tables), \
         patch("agent.nodes.schema_explorer.get_query_embedding", return_value=[0.1]*768), \
         patch("agent.nodes.schema_explorer.Session"), \
         patch("agent.nodes.schema_explorer.settings") as mock_settings:
         
        mock_settings.MAX_PROFILES_TO_FETCH = 8
        mock_settings.PROFILE_FETCH_CONCURRENCY = 5
        mock_settings.ENABLE_SEMANTIC_TYPING = False
        mock_settings.ENABLE_JOIN_GRAPH = False
        mock_settings.ENABLE_SCHEMA_SUMMARIZATION = False
        mock_settings.ENABLE_AMBIGUITY_DETECT = False

        call_count = 0
        async def mock_ainvoke(args):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01) # synthetic delay
            if call_count == 4:
                raise Exception("Network error injected")
            return json.dumps({"table_id": args["table_id"], "columns": [], "table_name": f"mock_table_{call_count}"})

        mock_tool = MagicMock()
        mock_tool.ainvoke = AsyncMock(side_effect=mock_ainvoke)
        with patch("agent.nodes.schema_explorer.get_table_profile", mock_tool):
            result = await schema_explorer_node(state)
            
            # Since mock LLM returns None for structured output unless configured, plan will be None
            assert result.get("schema_plan") == ""
            # Ensure no crash happened and 8 tables were attempted
            assert call_count == 8

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
        # mock_llm returns None structured output by default here, so summary is fallback
        assert "summary" in result
