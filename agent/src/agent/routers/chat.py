from sqlalchemy.ext.asyncio import AsyncSession

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from agent.graph import agent_graph
from python_core_utils.rate_limiting import RateLimiter
from langfuse.langchain import CallbackHandler

from sqlmodel import select
from core.db.engine import async_engine
from core.models.models import Table, HttpExtractor, ExtractorStatus
from langgraph.types import Command

from core.langfuse import get_langfuse_handler


router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class QueryApproval(BaseModel):
    approved: bool
    feedback: str | None = None


class ChatRequest(BaseModel):
    query: str | None = None
    thread_id: str | None = None
    resume_value: str | QueryApproval | None = None
    allowed_tables: list[str] | None = None
    allowed_statuses: list[str] | None = None
    extractors: list[str] | None = None
    hitl_enabled: bool = True


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed", "interrupted"
    interrupt_details: dict | None = None
    summary: str | None = None
    raw_data_ref: str | None = None
    sql_query: str | None = None
    sql_explanation: str | None = None
    schema_plan: str | None = None


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(RateLimiter(requests=10, window=60, fail_open=False))],
)
async def chat_endpoint(
    request: ChatRequest,
    langfuse_handler: CallbackHandler = Depends(get_langfuse_handler),
):
    thread_id = request.thread_id or str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [langfuse_handler] if langfuse_handler else [],
    }

    if request.resume_value is not None:
        state_snapshot = await agent_graph.aget_state(config)
        if not state_snapshot.values:
            raise HTTPException(
                status_code=404,
                detail=f"Thread ID '{thread_id}' not found or has no active session.",
            )

        resume_val = request.resume_value
        if isinstance(resume_val, QueryApproval):
            resume_val = resume_val.model_dump()

        result = await agent_graph.ainvoke(Command(resume=resume_val), config=config)
    else:
        if not request.query:
            raise HTTPException(
                status_code=400, detail="Query is required for new chat session."
            )

        if request.allowed_tables:
            async with AsyncSession(async_engine) as session:
                from sqlalchemy import or_
                for allowed in request.allowed_tables:
                    parts = allowed.split(".")
                    if len(parts) == 3:
                        cond = or_(
                            Table.id == allowed,
                            Table.name == allowed,
                            (Table.catalog == parts[0])
                            & (Table.schema_name == parts[1])
                            & (Table.name == parts[2]),
                        )
                    elif len(parts) == 2:
                        cond = or_(
                            Table.id == allowed,
                            Table.name == allowed,
                            (Table.schema_name == parts[0]) & (Table.name == parts[1]),
                        )
                    else:
                        cond = or_(Table.id == allowed, Table.name == allowed)
                    
                    exists = (await session.execute(select(Table.id).where(cond))).first()
                    if not exists:
                        raise HTTPException(
                            status_code=400, detail=f"Table '{allowed}' does not exist."
                        )

        active_extractors = []
        async with AsyncSession(async_engine) as session:
            if request.extractors:
                for ext_name_or_id in request.extractors:
                    ext = (
                        (
                            await session.execute(
                                select(HttpExtractor).where(
                                    (HttpExtractor.id == ext_name_or_id)
                                    | (HttpExtractor.name == ext_name_or_id)
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if not ext:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Extractor '{ext_name_or_id}' does not exist.",
                        )
                    active_extractors.append({"name": ext.name, "url": ext.url})
            else:
                prod_extractors = (
                    (
                        await session.execute(
                            select(HttpExtractor).where(
                                HttpExtractor.status == ExtractorStatus.production
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for ext in prod_extractors:
                    active_extractors.append({"name": ext.name, "url": ext.url})

        result = await agent_graph.ainvoke(
            {
                "user_query": request.query,
                "allowed_tables": request.allowed_tables,
                "allowed_statuses": request.allowed_statuses,
                "active_extractors": active_extractors,
                "non_interactive": not request.hitl_enabled,
            },
            config=config,
        )

    final_state = await agent_graph.aget_state(config)
    if final_state.interrupts:
        interrupt_val = final_state.interrupts[-1].value
        return ChatResponse(
            thread_id=thread_id,
            status="interrupted",
            interrupt_details=interrupt_val,
            schema_plan=final_state.values.get("schema_plan") or (interrupt_val.get("schema_plan") if isinstance(interrupt_val, dict) else None),
            sql_query=final_state.values.get("sql_query") or (interrupt_val.get("sql_query") if isinstance(interrupt_val, dict) else None),
        )

    return ChatResponse(
        thread_id=thread_id,
        status="completed",
        summary=result.get("summary", ""),
        raw_data_ref=result.get("raw_data_ref"),
        sql_query=result.get("sql_query"),
        sql_explanation=result.get("sql_explanation"),
        schema_plan=result.get("schema_plan"),
    )
