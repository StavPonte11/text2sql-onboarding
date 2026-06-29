from contextlib import asynccontextmanager
from typing import AsyncGenerator
from esca_sdk import EscaClient
from agent.config import settings


@asynccontextmanager
async def get_esca_client() -> AsyncGenerator[EscaClient, None]:
    """
    Manage the lifecycle of an Esca client.
    
    Ensures the client is closed when the context exits.
    
    Returns:
    	client (EscaClient): The initialized Esca client.
    """
    client = EscaClient(api_key=settings.ESCA_API_KEY, base_url=settings.ESCA_URL)
    try:
        yield client
    finally:
        await client.close()
