import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from agent.state import AgentState
from agent.nodes.satisfaction_check import satisfaction_check_node
from core.cache import CacheService
import json

@pytest.mark.asyncio
async def test_tts_g2_04_satisfaction_check_multi_stage_gate(mock_langfuse, mock_llm):
    # Base state
    state: AgentState = {
        "user_query": "test query",
        "sql_query": "SELECT *",
        "trino_error": None,
        "inline_result_rows": [{"col": "val"}], # 1 row
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
        # Default all other keys
        "messages": [], "query_enrichments": [], "schema_plan": "", "refinement_count": 0,
        "raw_data_ref": None, "summary": "", "sql_explanation": "", "allowed_tables": None,
        "allowed_statuses": None, "feedback": None, "feedback_route": None, "non_interactive": False,
        "active_extractors": None, "last_error": None, "hallucinated_tables": None,
        "esca_write_failed": None, "error_history": None, "schema_explorer_retry_count": 0,
        "escalated": None, "escalation_reason": None, "scoping_mode": "hybrid"
    }

    # Disable specific features except plausibility
    with patch("agent.nodes.satisfaction_check.settings") as mock_settings:
        mock_settings.SATISFACTION_CHECK_ENABLED = True
        mock_settings.SATISFACTION_CHECK_EXECUTION = False
        mock_settings.SATISFACTION_CHECK_PLAUSIBILITY = True
        mock_settings.SATISFACTION_MIN_ROWS = 2 # Setup to fail because we only have 1 row
        mock_settings.SATISFACTION_MAX_ROWS = 10
        mock_settings.SATISFACTION_MAX_FAILURES = 3
        mock_settings.SATISFACTION_CHECK_COLUMNS = False
        mock_settings.SATISFACTION_CHECK_SEMANTIC = False
        
        result = await satisfaction_check_node(state)
        
        assert result["satisfaction_fail_count"] == 1
        assert result["satisfaction_failures"] is not None
        assert "below minimum 2" in result["satisfaction_failures"][0]
        
    # Check execution failure
    state["trino_error"] = "SQL syntax error"
    with patch("agent.nodes.satisfaction_check.settings") as mock_settings:
        mock_settings.SATISFACTION_CHECK_ENABLED = True
        mock_settings.SATISFACTION_CHECK_EXECUTION = True
        mock_settings.SATISFACTION_CHECK_PLAUSIBILITY = False
        mock_settings.SATISFACTION_CHECK_COLUMNS = False
        mock_settings.SATISFACTION_CHECK_SEMANTIC = False
        mock_settings.SATISFACTION_MAX_FAILURES = 3
        
        result = await satisfaction_check_node(state)
        assert result["satisfaction_fail_count"] == 1
        assert "Execution failed" in result["satisfaction_failures"][0]

@pytest.mark.asyncio
async def test_tts_g2_05_redis_schema_cache_management_and_scan_eviction():
    # Setup mock Redis via CacheService
    # We will instantiate CacheService directly passing a dummy url and then patch its internal redis
    with patch("core.cache.aioredis.from_url") as mock_from_url:
        mock_redis_client = MagicMock()
        mock_redis_client.get = AsyncMock(return_value=b'{"cached": true}')
        mock_redis_client.setex = AsyncMock()
        mock_redis_client.delete = AsyncMock()
        mock_redis_client.scan = AsyncMock(side_effect=[(10, [b"profile:1:v1"]), (0, [b"profile:1:v2"])]) # Two batches
        
        # Mock pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.delete = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis_client.pipeline.return_value = mock_pipeline
        
        mock_from_url.return_value = mock_redis_client
        
        cache = CacheService("redis://dummy")
        
        # Verify read hit
        res = await cache.get_json("dummy_key")
        assert res == {"cached": True}
        
        # Verify setex respects SCHEMA_CACHE_TTL dynamically
        await cache.set_json("dummy_key", {"data": "test"}, 600)
        mock_redis_client.setex.assert_called_once_with("dummy_key", 600, b'{"data": "test"}')
        
        # Verify SCAN eviction for invalidate_profile
        await cache.invalidate_profile("1")
        
        # Should have called scan twice
        assert mock_redis_client.scan.call_count == 2
        # Should have called pipeline delete twice
        assert mock_pipeline.delete.call_count == 2
        mock_pipeline.delete.assert_any_call(b"profile:1:v1")
        mock_pipeline.delete.assert_any_call(b"profile:1:v2")
        # Should have executed the pipeline once
        mock_pipeline.execute.assert_called_once()
