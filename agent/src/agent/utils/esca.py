from contextlib import asynccontextmanager
from typing import AsyncGenerator
from esca_sdk import EscaClient
from agent.config import settings


@asynccontextmanager
async def get_esca_client() -> AsyncGenerator[EscaClient, None]:
    """
    Asynchronous context manager to encapsulate EscaClient lifecycle.
    Ensures that the client connection is always cleanly closed.
    """
    client = EscaClient(api_key=settings.ESCA_API_KEY, base_url=settings.ESCA_URL)
    try:
        yield client
    finally:
        await client.close()
