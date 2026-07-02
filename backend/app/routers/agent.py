"""
agent.py — Backend proxy for the Text2SQL Agent MCP service.

The backend is the sole caller of the agent's MCP server.
The frontend never talks to the agent directly; it goes through these endpoints.

Endpoints:
  POST /agent/chat        — Start a new query or resume after human approval/rejection
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
import redis.asyncio as redis
from core.db.engine import get_session
from core.models.models import Table, TableRead, TableStatus
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel
from sqlmodel import Session, select

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
    resume_value: QueryApproval | str | dict | None = None
    allowed_tables: list[str] | None = None
    allowed_statuses: list[str] | None = None
    extractors: list[str] | None = None
    active_skills: list[str] | None = None
    execution_mode: str | None = None
    hitl_enabled: bool = True


class SuggestFixesRequest(BaseModel):
    thread_id: str
    category: str


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "interrupted"
    interrupt_details: dict | None = None
    summary: str | None = None
    raw_data_ref: str | None = None
    sql_query: str | None = None
    sql_explanation: str | None = None
    schema_plan: str | None = None
    trace_id: str | None = None
    execution_path: list[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _get_mcp_client():
    url = f"{settings.AGENT_URL}/mcp"
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def _call_agent_mcp(tool_arguments: dict) -> dict:
    """
    Connects to the agent MCP server over Streamable HTTP, initializes the session,
    calls the 'chat_with_agent' tool, and returns the parsed result.
    """
    url = f"{settings.AGENT_URL}/mcp"
    logger.debug("Connecting to agent MCP: %s  args=%s", url, tool_arguments)

    try:
        async with streamablehttp_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Call the tool using the MCP client session
                result = await session.call_tool(
                    "chat_with_agent",
                    arguments=tool_arguments,
                    read_timeout_seconds=timedelta(seconds=300.0),
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

                if getattr(result, "isError", False):
                    error_text = (
                        first.text
                        if first and hasattr(first, "text")
                        else "Unknown MCP Error"
                    )
                    raise HTTPException(
                        status_code=502, detail=f"Agent returned error: {error_text}"
                    )

                try:
                    tool_result = json.loads(first.text)
                except json.JSONDecodeError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Agent returned non-JSON content: {first.text[:100]}... ({exc})",
                    )

                if "error" in tool_result:
                    raise HTTPException(status_code=400, detail=tool_result["error"])

                return tool_result

    except HTTPException:
        raise
    except BaseExceptionGroup as eg:
        http_exc = next(
            (e for e in eg.exceptions if isinstance(e, HTTPException)), None
        )
        if http_exc:
            raise http_exc
        logger.error(f"MCP client error: {eg}", exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"Failed to communicate with Agent MCP: {eg}"
        )
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


@router.get("/tables", response_model=list[TableRead])
def get_agent_tables(
    status: TableStatus | None = None, session: Session = Depends(get_session)
):
    """
    Internal endpoint for the agent and evaluation service to fetch available tables
    without requiring user SSO authentication.
    """
    q = select(Table)
    if status:
        q = q.where(Table.status == status)
    return session.exec(q).all()


@router.get("/stream/{thread_id}")
async def stream_agent_execution(thread_id: str):
    """Subscribe to Redis PubSub for agent graph execution events and yield them as SSE."""

    async def event_generator():
        r = None
        pubsub = None
        try:
            r = redis.from_url(
                settings.REDIS_URL, health_check_interval=30, retry_on_timeout=True
            )
            pubsub = r.pubsub()
            await pubsub.subscribe(f"agent_stream:{thread_id}")
            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message and message["type"] == "message":
                        data = message["data"].decode("utf-8")
                        yield f"data: {data}\n\n"
                    else:
                        yield ": keep-alive\n\n"
                except (redis.exceptions.TimeoutError, TimeoutError) as e:
                    logger.debug("Redis read timeout, retrying... %s", e)
                    yield ": keep-alive\n\n"
                    continue
                except redis.exceptions.ConnectionError as e:
                    logger.warning(
                        "Redis connection error, attempting to reconnect... %s", e
                    )
                    await asyncio.sleep(1)
                    try:
                        if pubsub:
                            await pubsub.close()
                        pubsub = r.pubsub()
                        await pubsub.subscribe(f"agent_stream:{thread_id}")
                    except Exception as reconnect_err:
                        logger.error("Failed to reconnect to Redis: %s", reconnect_err)
                        await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Unhandled error in event generator: %s", e, exc_info=True)
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/traces/{trace_id}")
async def get_trace_timeline(trace_id: str):
    """Fetch trace from Langfuse and normalize observations for frontend timeline."""
    auth = (settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY)
    url = f"{settings.LANGFUSE_HOST}/api/public/traces/{trace_id}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code != 200:
            if resp.status_code == 404:
                return []
            raise HTTPException(
                status_code=resp.status_code, detail=f"Langfuse error: {resp.text}"
            )

        data = resp.json()
        observations = data.get("observations", [])

        # Normalize
        timeline = []
        for obs in observations:
            start_time_str = obs.get("startTime")
            end_time_str = obs.get("endTime")
            duration_ms = 0
            if start_time_str and end_time_str:
                try:
                    start_dt = datetime.fromisoformat(
                        start_time_str.replace("Z", "+00:00")
                    )
                    end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
                except Exception:
                    pass

            timeline.append(
                {
                    "span_name": obs.get("name") or obs.get("type"),
                    "start_time": start_time_str,
                    "duration_ms": duration_ms,
                    "input_tokens": obs.get("promptTokens", 0),
                    "output_tokens": obs.get("completionTokens", 0),
                    "model": obs.get("model") or "N/A",
                    "status": "success" if not obs.get("statusMessage") else "error",
                    "input_preview": str(obs.get("input", "")),
                    "output_preview": str(obs.get("output", "")),
                }
            )

        # Sort by start time
        timeline.sort(key=lambda x: x["start_time"] or "")
        return timeline


@router.post("/suggest_fixes")
async def suggest_fixes(req: SuggestFixesRequest):
    """Generate quick fixes during HITL interruption via MCP."""
    try:
        async with _get_mcp_client() as session:
            result = await session.call_tool(
                "suggest_fixes",
                arguments={"thread_id": req.thread_id, "category": req.category},
                read_timeout_seconds=timedelta(seconds=300.0),
            )
            content = result.content[0].text
            return json.loads(content)
    except Exception as e:
        logger.error(f"Suggest fixes error: {e}")
        return []
