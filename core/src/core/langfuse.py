from langfuse.langchain import CallbackHandler
from core.config import settings

import logging
logger = logging.getLogger(__name__)

def get_langfuse_handler() -> CallbackHandler | None:
    """FastAPI dependency to inject an isolated Langfuse CallbackHandler."""
    try:
        return CallbackHandler()
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse CallbackHandler: {e}")
        return None
