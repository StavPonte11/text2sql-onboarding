import operator
from typing import Annotated, TypedDict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    user_query: str
    messages: Annotated[list[BaseMessage], add_messages]
    extracted_entities: dict[str, Any]
    schema_plan: str
    sql_query: str
    trino_error: str | None
    refinement_count: int
    raw_data_ref: str | None
    summary: str
    sql_explanation: str
    allowed_tables: list[str] | None
