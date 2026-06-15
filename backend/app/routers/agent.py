"""
agent.py — Backend proxy for the Text2SQL Agent MCP service.

The backend is the sole caller of the agent's MCP server.
The frontend never talks to the agent directly; it goes through these endpoints.

Endpoints:
  POST /agent/chat        — Start a new query or resume after human approval/rejection
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class QueryApproval(BaseModel):
    approved: bool
    feedback: str | None = None


class ChatRequest(BaseModel):
    query: str | None = None
    thread_id: str | None = None
    resume_value: QueryApproval | str | None = None
    allowed_tables: list[str] | None = None
    allowed_statuses: list[str] | None = None
    extractors: list[str] | None = None
    hitl_enabled: bool = True


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "interrupted"
    interrupt_details: dict | None = None
    summary: str | None = None
    raw_data_ref: str | None = None
    sql_query: str | None = None
    sql_explanation: str | None = None
    schema_plan: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_agent_mcp(tool_arguments: dict) -> dict:
    """
    Connects to the agent MCP server over SSE, initializes the session,
    calls the 'chat_with_agent' tool, and returns the parsed result.
    """
    url = f"{settings.AGENT_URL}/sse"
    logger.debug("Connecting to agent MCP: %s  args=%s", url, tool_arguments)

    try:
        async with sse_client(url) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()

                # Call the tool using the MCP client session
                result = await session.call_tool(
                    "chat_with_agent", arguments=tool_arguments
                )

                if not result.content:
                    raise HTTPException(
                        status_code=502, detail="Agent returned empty content."
                    )

                first = result.content[0]
                if first.type != "text":
                    raise HTTPException(
                        status_code=502, detail="Unexpected content type from agent."
                    )

                try:
                    tool_result = json.loads(first.text)
                except json.JSONDecodeError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Agent returned non-JSON content: {exc}",
                    )

                if "error" in tool_result:
                    raise HTTPException(status_code=400, detail=tool_result["error"])

                return tool_result

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"MCP client error: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"Failed to communicate with Agent MCP: {exc}"
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Start a new agent session or resume an interrupted one.

    - First call: provide `query` (and optionally `allowed_tables`, `allowed_statuses`, `extractors`).
    - Resume call: provide `thread_id` + `resume_value` (`{"approved": true}` or
      `{"approved": false, "feedback": "..."}`).

    The backend forwards the request to the agent MCP service and returns the
    structured result. The frontend only deals with this endpoint.
    """
    # Build the arguments dictionary for the MCP tool
    resume_val = None
    if request.resume_value is not None:
        if isinstance(request.resume_value, QueryApproval):
            resume_val = request.resume_value.model_dump()
        else:
            resume_val = request.resume_value

    tool_arguments: dict = {
        "query": request.query,
        "thread_id": request.thread_id,
        "resume_value": resume_val,
        "allowed_tables": request.allowed_tables,
        "allowed_statuses": request.allowed_statuses,
        "extractors": request.extractors,
        "hitl_enabled": request.hitl_enabled,
    }

    result = await _call_agent_mcp(tool_arguments)
    return ChatResponse(**result)
