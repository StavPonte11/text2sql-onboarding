import logging
import sys
from typing import Any, Dict
from pydantic import BaseModel, Field, ConfigDict
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

# ---------------------------------------------------------
# Structlog Initial Setup & Configuration
# ---------------------------------------------------------

def configure_logging() -> None:
    """
    Configures standard library logging and structlog to output structured JSON.
    Uses merge_contextvars to enable thread-safe and async-safe context propagation.
    """
    # Intercept standard library logging and direct to stdout
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    structlog.configure(
        processors=[
            # Filter logs based on severity levels
            structlog.stdlib.filter_by_level,
            # Merge ContextVars into the log payload (used for request context propagation)
            structlog.contextvars.merge_contextvars,
            # Add log levels (e.g., info, error)
            structlog.processors.add_log_level,
            # Format exception traces cleanly if present
            structlog.processors.format_exc_info,
            # Attach a high-resolution ISO 8601 timestamp
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Final output is rendered in standard JSON format for observability pipelines (e.g., Splunk)
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# Run configuration upon import
configure_logging()
logger = structlog.get_logger("text2sql")


# ---------------------------------------------------------
# Request Context Propagation Helpers (ContextVars)
# ---------------------------------------------------------

def bind_request_context(
    session_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    langfuse_trace_id: str | None = None,
) -> None:
    """
    Binds request identifiers to structlog's thread-local and async-local ContextVars.
    These fields will automatically appear in all downstream logs produced within the active async task.
    """
    context_data: Dict[str, Any] = {}
    if session_id is not None:
        context_data["session_id"] = session_id
    if user_id is not None:
        context_data["user_id"] = user_id
    if request_id is not None:
        context_data["request_id"] = request_id
    if langfuse_trace_id is not None:
        context_data["langfuse_trace_id"] = langfuse_trace_id
        
    bind_contextvars(**context_data)


def clear_request_context() -> None:
    """
    Clears all active context variables for the current async task execution context.
    """
    clear_contextvars()


# ---------------------------------------------------------
# Pydantic Schemas for Structured Logging
# ---------------------------------------------------------

class NodeEvent(BaseModel):
    """
    Pydantic schema representing a structured LangGraph node completion event.
    """
    model_config = ConfigDict(populate_by_name=True)

    event: str = Field(..., description="Action or state represented by the log")
    node_name: str = Field(..., description="The executing node name inside LangGraph")
    session_id: str | None = Field(None, description="Active user session ID")
    request_id: str | None = Field(None, description="API HTTP Request ID")
    duration_ms: int = Field(..., description="Node execution latency in milliseconds")
    langfuse_trace_id: str | None = Field(None, description="Langfuse trace tracker ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual fields")


def LOG_NODE_EVENT(event: NodeEvent) -> None:
    """
    Structured logging handler that takes a validated NodeEvent and writes it as JSON.
    Auto-binds any attributes stored in metadata as root fields in the final JSON.
    """
    # Structlog automatically merges ContextVars, but we explicitly pass fields 
    # to guarantee they are mapped cleanly to the JSON payload.
    logger.info(
        event.event,
        node_name=event.node_name,
        duration_ms=event.duration_ms,
        session_id=event.session_id,
        request_id=event.request_id,
        langfuse_trace_id=event.langfuse_trace_id,
        **event.metadata
    )
