from langfuse.langchain import CallbackHandler
from core.config import settings

import logging
logger = logging.getLogger(__name__)

try:
    _langfuse_handler = CallbackHandler(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_BASE_URL
    )
except Exception as e:
    logger.error(f"Failed to initialize Langfuse CallbackHandler: {e}")
    _langfuse_handler = None

def get_langfuse_handler() -> CallbackHandler | None:
    """
    Provide the module-level Langfuse callback handler.
    
    Returns:
        CallbackHandler | None: The initialized Langfuse callback handler, or ``None`` if initialization failed.
    """
    return _langfuse_handler
