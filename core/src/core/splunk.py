import asyncio
import logging
import os
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import structlog

# We use the splunk-hec-handler library which enables sending dictionary payloads directly
from splunk_hec_handler import SplunkHecHandler

# Local fallback logger in case Splunk connection breaks
local_logger = structlog.get_logger("splunk_integration")

# Map abstract event identifiers to production Splunk sourcetypes
SOURCETYPES: Dict[str, str] = {
    "query_execution": "text2sql:query_execution",
    "schema_hallucination": "text2sql:schema_hallucination",
    "esca_failure": "text2sql:esca_failure",
    "agent_node_error": "text2sql:agent_node_error",
}

# Cache initialized loggers to reuse connection pools and prevent file/socket descriptor leaks
_splunk_loggers: Dict[str, logging.Logger] = {}


def get_splunk_logger(sourcetype: str) -> Optional[logging.Logger]:
    """
    Returns a configured Splunk logger for the given sourcetype.
    If HEC configuration details are missing, returns None for local fallback.
    """
    host = os.getenv("SPLUNK_HEC_HOST")
    token = os.getenv("SPLUNK_HEC_TOKEN")

    if not host or not token:
        return None

    if sourcetype in _splunk_loggers:
        return _splunk_loggers[sourcetype]

    port = int(os.getenv("SPLUNK_HEC_PORT", "8088"))
    proto = os.getenv("SPLUNK_HEC_PROTO", "https")
    ssl_verify = os.getenv("SPLUNK_HEC_SSL_VERIFY", "true").lower() == "true"
    index = os.getenv("SPLUNK_HEC_INDEX", "main")
    source = os.getenv("SPLUNK_HEC_SOURCE", "text2sql-platform")

    handler = SplunkHecHandler(
        host=host,
        token=token,
        port=port,
        proto=proto,
        ssl_verify=ssl_verify,
        index=index,
        source=source,
        sourcetype=sourcetype
    )

    logger_name = f"splunk_{sourcetype.replace(':', '_')}"
    splunk_logger = logging.getLogger(logger_name)
    splunk_logger.setLevel(logging.INFO)
    splunk_logger.addHandler(handler)
    
    # Avoid duplicate local logging by disabling console/root propagation
    splunk_logger.propagate = False

    _splunk_loggers[sourcetype] = splunk_logger
    return splunk_logger


async def splunk_log(event: Dict[str, Any], sourcetype: str) -> None:
    """
    Asynchronously logs structured dictionaries to Splunk HEC.
    Uses asyncio.to_thread to run HEC HTTP calls in a threadpool to protect FastAPI's event loop.
    Fails safely, writing connection or validation errors to local structlog.
    """
    try:
        # Map to mapped Splunk sourcetype
        mapped_sourcetype = SOURCETYPES.get(sourcetype, sourcetype)
        splunk_logger = get_splunk_logger(mapped_sourcetype)

        if splunk_logger is None:
            # Fallback to local log
            local_logger.debug(
                "Splunk HEC not configured. Event output sent to local debug log.",
                event_payload=event,
                sourcetype=mapped_sourcetype
            )
            return

        # Execute HEC HTTP payload dispatch on a separate worker thread
        await asyncio.to_thread(splunk_logger.info, event)

    except Exception as exc:
        local_logger.error(
            "Failed to send event to Splunk HEC",
            error=str(exc),
            sourcetype=sourcetype,
            event_payload=event
        )


# ---------------------------------------------------------
# Event Data Schemas
# ---------------------------------------------------------

class QueryExecutionEvent(BaseModel):
    """
    Schema for recording structured audit traces of successfully generated
    and executed SQL statements.
    """
    session_id: str | None = Field(None, description="Active session ID")
    request_id: str | None = Field(None, description="HTTP API Request ID")
    user_id: str | None = Field(None, description="UUID of the triggering user")
    final_sql: str = Field(..., description="The final generated SQL query executed against Trino")
    refiner_iterations: int = Field(0, description="Total attempts taken by the agent refiner")
    execution_duration_ms: int = Field(..., description="Execution duration in milliseconds")
    langfuse_trace_id: str | None = Field(None, description="Langfuse tracking ID")


class SchemaHallucinationEvent(BaseModel):
    """
    Schema for tracking database table query hallucinations (e.g. querying tables
    that do not exist in the database catalog).
    """
    table_name: str = Field(..., description="The hallucinated table name")
    query: str = Field(..., description="The original user query text")
    session_id: str | None = Field(None, description="Active session ID")
    request_id: str | None = Field(None, description="HTTP API Request ID")


class EscaFailureEvent(BaseModel):
    """
    Schema for tracking failures in the ESCA payload cache layer.
    """
    failure_type: str = Field(..., description="Category of failure (e.g., Timeout, Auth, Connection)")
    error_message: str = Field(..., description="Details of the error response")
    session_id: str | None = Field(None, description="Active session ID")
    request_id: str | None = Field(None, description="HTTP API Request ID")
