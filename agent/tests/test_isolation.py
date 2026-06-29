import pytest
import asyncio
import os
import uuid
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_tts_g1_02_langfuse_handler_isolation(mock_langfuse):
    from core.langfuse import get_langfuse_handler
    
    # Simulate concurrent requests to /chat by generating multiple handlers concurrently
    async def simulate_request():
        # Each request gets an isolated CallbackHandler
        handler = get_langfuse_handler()
        return handler

    # Gather 10 concurrent handler requests
    results = await asyncio.gather(*[simulate_request() for _ in range(10)])

    # Assert they are all unique isolated instances
    handlers_set = set()
    for handler in results:
        assert handler is not None
        assert id(handler) not in handlers_set
        handlers_set.add(id(handler))
        
    assert len(handlers_set) == 10


