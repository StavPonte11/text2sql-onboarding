import os
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from agent.graph import agent_graph
from python_core_utils.rate_limiting import RateLimiter
from langfuse.langchain import CallbackHandler
from agent.config import settings
from sqlmodel import Session, select
from core.db.engine import engine
from core.models.models import Table
from langgraph.types import Command

# Set the environment variables for Langfuse from the validated Pydantic settings config
os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_BASE_URL

# Initialize Langfuse handler
langfuse_handler = CallbackHandler()

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

class QueryApproval(BaseModel):
    approved: bool
    feedback: str | None = None

class ChatRequest(BaseModel):
    query: str | None = None
    thread_id: str | None = None
    resume_value: str | QueryApproval | None = None
    allowed_tables: list[str] | None = None


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
    dependencies=[Depends(RateLimiter(requests=10, window=60, fail_open=False))]
)
async def chat_endpoint(request: ChatRequest):
    thread_id = request.thread_id or str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [langfuse_handler]
    }

    if request.resume_value is not None:
        state_snapshot = await agent_graph.aget_state(config)
        if not state_snapshot.values:
            raise HTTPException(
                status_code=404,
                detail=f"Thread ID '{thread_id}' not found or has no active session."
            )
            
        resume_val = request.resume_value
        if isinstance(resume_val, QueryApproval):
            resume_val = resume_val.model_dump()
            
        result = await agent_graph.ainvoke(
            Command(resume=resume_val),
            config=config
        )
    else:
        if not request.query:
            raise HTTPException(
                status_code=400,
                detail="Query is required for new chat session."
            )
            
        if request.allowed_tables:
            with Session(engine) as session:
                all_tables = session.exec(select(Table)).all()
                for allowed in request.allowed_tables:
                    exists = False
                    for t in all_tables:
                        if (t.id == allowed or 
                            t.name == allowed or 
                            f"{t.schema_name}.{t.name}" == allowed):
                            exists = True
                            break
                    if not exists:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Table '{allowed}' does not exist."
                        )

        result = await agent_graph.ainvoke(
            {"user_query": request.query, "allowed_tables": request.allowed_tables},
            config=config
        )

    final_state = await agent_graph.aget_state(config)
    if final_state.interrupts:
        interrupt_val = final_state.interrupts[-1].value
        return ChatResponse(
            thread_id=thread_id,
            status="interrupted",
            interrupt_details=interrupt_val,
            schema_plan=final_state.values.get("schema_plan"),
            sql_query=final_state.values.get("sql_query")
        )

    return ChatResponse(
        thread_id=thread_id,
        status="completed",
        summary=result.get("summary", ""),
        raw_data_ref=result.get("raw_data_ref"),
        sql_query=result.get("sql_query"),
        sql_explanation=result.get("sql_explanation"),
        schema_plan=result.get("schema_plan")
    )


