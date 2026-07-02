from typing import Annotated, TypedDict, Any, Literal
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
import operator


class AgentState(TypedDict):
    user_query: str
    execution_path: Annotated[list[str], operator.add]
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
    rejection_category: str | None
    feedback_route: str | None
    non_interactive: bool | None
    active_extractors: list[dict[str, str]] | None
    active_skills: list[str] | None
    loaded_skills: list[dict] | None
    last_error: str | None
    hallucinated_tables: list[str] | None
    esca_write_failed: bool | None
    inline_result_rows: list[list[Any]] | None
    inline_result_columns: list[str] | None
    error_history: list[str] | None
    schema_explorer_retry_count: int | None
    # G2-01: table scoping
    scoping_mode: Literal["strict", "hybrid"] | None  # controlled via config / runtime_flags
    # G2-02: HITL escalation
    escalated: bool | None
    escalation_reason: str | None
    # G2-04: satisfaction check
    satisfaction_failures: list[str] | None
    satisfaction_fail_count: int | None
    # G4: feature flags & execution modes
    execution_mode: str | None          # e.g. "cost_saving", "high_quality", "benchmark"
    runtime_flags: dict[str, Any] | None  # resolved by init_flags_node
    # Enriched table profiles — populated by schema_explorer for reuse by refiner
    table_profiles: list[dict[str, Any]] | None
