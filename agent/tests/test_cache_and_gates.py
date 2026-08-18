import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from agent.state import AgentState
from core.cache import CacheService
import json


@pytest.mark.asyncio
async def test_tts_g2_05_redis_schema_cache_management_and_scan_eviction():
    # Setup mock Redis via CacheService
    # We will instantiate CacheService directly passing a dummy url and then patch its internal redis
    with patch("redis.asyncio.from_url") as mock_from_url:
        mock_redis_client = MagicMock()
        mock_redis_client.get = AsyncMock(return_value=b'{"cached": true}')
        mock_redis_client.setex = AsyncMock()
        mock_redis_client.delete = AsyncMock()
        mock_redis_client.scan = AsyncMock(
            side_effect=[(10, [b"profile:1:v1"]), (0, [b"profile:1:v2"])]
        )  # Two batches

        # Mock pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.delete = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_redis_client.pipeline.return_value = mock_pipeline

        mock_from_url.return_value = mock_redis_client

        cache = CacheService()
        cache._redis = mock_redis_client

        # Verify read hit
        res = await cache.get_json("dummy_key")
        assert res == {"cached": True}

        # Verify setex respects SCHEMA_CACHE_TTL dynamically
        await cache.set_json("dummy_key", {"data": "test"}, 600)
        mock_redis_client.setex.assert_called_once_with(
            "dummy_key", 600, b'{"data": "test"}'
        )

        # Verify SCAN eviction for invalidate_profile
        await cache.invalidate_profile("1")

        # Should have called scan twice
        assert mock_redis_client.scan.call_count == 2
        # Should have called pipeline delete twice
        assert mock_pipeline.delete.call_count == 2
        mock_pipeline.delete.assert_any_call(b"profile:1:v1")
        mock_pipeline.delete.assert_any_call(b"profile:1:v2")
        assert mock_pipeline.execute.call_count == 2
