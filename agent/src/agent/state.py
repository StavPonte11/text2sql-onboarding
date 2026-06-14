import operator
from typing import Annotated, TypedDict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    user_query: str
    messages: Annotated[list[BaseMessage], add_messages]
    query_enrichments: list[dict[str, Any]]
    schema_plan: str
    sql_query: str
    trino_error: str | None
    refinement_count: int
    raw_data_ref: str | None
    summary: str
    sql_explanation: str
    allowed_tables: list[str] | None
    allowed_statuses: list[str] | None
    feedback: str | None
    feedback_route: str | None
    non_interactive: bool | None
    active_extractors: list[dict[str, str]] | None

