from langfuse.langchain import CallbackHandler
from core.config import settings

_langfuse_handler = CallbackHandler(
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    secret_key=settings.LANGFUSE_SECRET_KEY,
    host=settings.LANGFUSE_BASE_URL
)

def get_langfuse_handler() -> CallbackHandler:
    """FastAPI dependency to inject the Langfuse CallbackHandler singleton."""
    return _langfuse_handler
