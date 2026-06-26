"""
LangGraph agent graph — Group 2 hardened topology.

Node order:
  START → validate_config → extractor → schema_explorer → ...
          (G2-01 fail-fast)

HITL escalation compiles with interrupt_before=["hitl_escalation"] so
LangGraph pauses before executing that node.  After a human injects
corrected state the graph resumes from hitl_escalation which immediately
routes to extractor (full state reset path).

Satisfaction check sits between refiner success path and finalizer (G2-04).
"""

from agent.nodes.refiner import MAX_REFINER_ITERATIONS
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.runnables.config import RunnableConfig
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.utils.redis_publisher import publish_node_event_sync
from agent.nodes.extractor import extractor_node
from agent.nodes.init_flags import init_flags_node
from agent.nodes.init_skills import init_skills_node
from agent.nodes.schema_explorer import schema_explorer_node, MAX_SCHEMA_RETRIES
from agent.nodes.query_builder import query_builder_node
from agent.nodes.refiner import refiner_node
from agent.nodes.finalizer import finalizer_node
from agent.nodes.satisfaction_check import satisfaction_check_node
from agent.config import settings
from agent.langfuse_client import langfuse_client
from pydantic import BaseModel, Field
from typing import Literal

from agent.llm import get_llm

# Initialize LLM for rejection routing classification
llm = get_llm("routing")


# ── G2-01: Custom exception ───────────────────────────────────────────────────


class InvalidConfigurationException(ValueError):
    """Raised when agent state contains an invalid or unsafe configuration."""


# ── G2-01: Config validator node ──────────────────────────────────────────────


def validate_config_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """
    First node after START.  Resolves scoping_mode from state (or falls back
    to the env default) and enforces strict-mode preconditions.

    Raises:
        InvalidConfigurationException: if scoping_mode='strict' and
            allowed_tables is null or empty.
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "validate_config")

    mode: str = state.get("scoping_mode") or settings.TABLE_SCOPING_MODE

    if mode == "strict":
        allowed = state.get("allowed_tables")
        if not allowed:
            raise InvalidConfigurationException(
                "scoping_mode='strict' requires allowed_tables to be a non-empty list. "
                "Execution aborted to prevent unrestricted table access."
            )

    return {"scoping_mode": mode, "execution_path": ["validate_config"]}


# ── G2-02: HITL escalation node ───────────────────────────────────────────────


def hitl_escalation_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """
    Execution pauses HERE via LangGraph interrupt_before before this node runs.
    The human then calls graph.update_state() to inject corrected state and
    clears sql_query / last_error / trino_error / escalated / escalation_reason.
    After update_state the graph resumes from this node, which immediately
    routes to extractor via its direct edge.

    This node body only performs observability work — it does NOT call interrupt()
    itself (interrupt_before handles the pause at compile time).
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "hitl_escalation")

    reason = state.get("escalation_reason", "Maximum retries exhausted.")

    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client._create_trace_tags_via_ingestion(
                trace_id=trace_id, tags=["escalated=True"]
            )
            langfuse_client.update_current_span(
                metadata={"escalation_reason": reason},
            )
    except Exception:
        pass

    return {"escalated": True, "execution_path": ["hitl_escalation"]}


# ── Rejection router ──────────────────────────────────────────────────────────


class RejectionRoute(BaseModel):
    route: Literal["extractor", "schema_explorer", "query_builder"] = Field(
        description="The phase to route the execution back to based on the user feedback."
    )


def rejection_router_node(state: AgentState, config: RunnableConfig | None = None):
    """Classify user rejection feedback and select the appropriate phase to return to."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "rejection_router")

    feedback = state.get("feedback")
    category = state.get("rejection_category")

    if category == "Wrong Tables":
        return {"feedback_route": "schema_explorer"}
    elif category == "Wrong Logic":
        return {"feedback_route": "query_builder"}

    if not feedback:
        return {"feedback_route": "query_builder"}

    langfuse_prompt = langfuse_client.get_prompt(
        settings.LANGFUSE_PROMPT_REJECTION_ROUTER
    )
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())

    structured_llm = llm.with_structured_output(RejectionRoute, method="json_schema")
    chain = prompt | structured_llm
    try:
        response = chain.invoke({"feedback": feedback})
        route = response.route
    except Exception as e:
        print(f"Structured output parsing failed: {e}")
        route = "extractor"  # Fallback

    return {
        "feedback_route": route,
        "sql_query": "",
        "schema_plan": "",
        "raw_data_ref": None,
        "trino_error": None,
        "execution_path": ["rejection_router"]
    }


# ── Conditional edge functions ────────────────────────────────────────────────


def route_schema_explorer(state: AgentState) -> str:
    """G2-02: route to hitl_escalation after MAX_SCHEMA_RETRIES."""
    if state.get("hallucinated_tables"):
        if (state.get("schema_explorer_retry_count") or 0) >= MAX_SCHEMA_RETRIES:
            return "hitl_escalation"
        return "schema_explorer"
    return "query_builder"


def route_refiner(state: AgentState) -> str:
    """G2-02: route to hitl_escalation when refiner limit is hit."""
    if state.get("trino_error"):
        if state.get("refinement_count", 0) < MAX_REFINER_ITERATIONS:
            return "satisfaction_check"  # run check even on error path so Check A can flag it
        return "hitl_escalation"
    return "satisfaction_check"


def route_satisfaction(state: AgentState) -> str:
    """
    G2-04: route based on satisfaction check outcome.
      - no module / no failures  → finalizer
      - failures, under MAX      → refiner
      - failures, over MAX       → hitl_escalation
    """
    failures = state.get("satisfaction_failures")
    if not failures:
        return "finalizer"

    fail_count = state.get("satisfaction_fail_count") or 0
    if fail_count >= settings.SATISFACTION_MAX_FAILURES:
        return "hitl_escalation"
    return "refiner"


def route_query_builder(state: AgentState) -> str:
    if state.get("feedback"):
        return "rejection_router"
    return "refiner"


def route_rejection(state: AgentState) -> str:
    route = state.get("feedback_route")
    if route in ["extractor", "schema_explorer", "query_builder"]:
        return route
    return "extractor"


# ── Build graph ───────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("validate_config", validate_config_node)
workflow.add_node("init_flags", init_flags_node)
workflow.add_node("init_skills", init_skills_node)
workflow.add_node("extractor", extractor_node)
workflow.add_node("schema_explorer", schema_explorer_node)
workflow.add_node("query_builder", query_builder_node)
workflow.add_node("rejection_router", rejection_router_node)
workflow.add_node("refiner", refiner_node)
workflow.add_node("satisfaction_check", satisfaction_check_node)
workflow.add_node("hitl_escalation", hitl_escalation_node)
workflow.add_node("finalizer", finalizer_node)

# Entry: validate config → resolve flags → load skills → start reasoning
workflow.add_edge(START, "validate_config")
workflow.add_edge("validate_config", "init_flags")
workflow.add_edge("init_flags", "init_skills")
workflow.add_edge("init_skills", "extractor")
workflow.add_edge("extractor", "schema_explorer")

workflow.add_conditional_edges(
    "schema_explorer",
    route_schema_explorer,
    {
        "schema_explorer": "schema_explorer",
        "query_builder": "query_builder",
        "hitl_escalation": "hitl_escalation",  # G2-02
    },
)

workflow.add_conditional_edges(
    "query_builder",
    route_query_builder,
    {"rejection_router": "rejection_router", "refiner": "refiner"},
)

workflow.add_conditional_edges(
    "rejection_router",
    route_rejection,
    {
        "extractor": "extractor",
        "schema_explorer": "schema_explorer",
        "query_builder": "query_builder",
    },
)

workflow.add_conditional_edges(
    "refiner",
    route_refiner,
    {
        "satisfaction_check": "satisfaction_check",  # G2-04 replaces direct → finalizer
        "hitl_escalation": "hitl_escalation",         # G2-02
    },
)

# G2-04: satisfaction gate
workflow.add_conditional_edges(
    "satisfaction_check",
    route_satisfaction,
    {
        "finalizer": "finalizer",
        "refiner": "refiner",
        "hitl_escalation": "hitl_escalation",
    },
)

# G2-02: HITL resume path → restart from extractor (full state reset by human)
workflow.add_edge("hitl_escalation", "extractor")
workflow.add_edge("finalizer", END)

memory = MemorySaver()
agent_graph = workflow.compile(
    checkpointer=memory,
    interrupt_before=["hitl_escalation"],  # G2-02: pause before HITL node
)
