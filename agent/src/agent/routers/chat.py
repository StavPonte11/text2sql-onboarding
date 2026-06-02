import os
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from agent.graph import agent_graph
from python_core_utils.rate_limiting import RateLimiter
from langfuse.langchain import CallbackHandler
from agent.config import settings

# Set the environment variables for Langfuse from the validated Pydantic settings config
os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_BASE_URL

# Initialize Langfuse handler
langfuse_handler = CallbackHandler()

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

class ChatRequest(BaseModel):
    query: str

class ChatResponse(BaseModel):
    summary: str
    raw_data_ref: str | None
    sql_query: str | None
    sql_explanation: str | None



@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(RateLimiter(requests=10, window=60, fail_open=False))]
)
async def chat_endpoint(request: ChatRequest):
    result = await agent_graph.ainvoke(
        {"user_query": request.query},
        config={"callbacks": [langfuse_handler]}
    )
    return ChatResponse(
        summary=result.get("summary", ""),
        raw_data_ref=result.get("raw_data_ref"),
        sql_query=result.get("sql_query"),
        sql_explanation=result.get("sql_explanation")
    )

