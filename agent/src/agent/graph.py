"""
LangGraph agent graph — Group 2 hardened topology.

Node order:
  START → init_flags → validate_config
                     → init_skills → extractor → schema_explorer
                                                    → sql_static_validations
                                                    → detect_ambiguity
                                                         ├─ [clear]        → query_builder → ...
                                                         ├─ [ambiguous]    → ambiguity_resolution  ← interrupt
                                                         │                      └─ schema_explorer (retry)
                                                         └─ [unanswerable] → END

HITL escalation compiles with interrupt_before=["hitl_escalation", "ambiguity_resolution"] so
LangGraph pauses before executing either node. After a human injects corrected
state (feedback / clarification) the graph resumes:
  • hitl_escalation   → extractor (full reset path for hard errors)
  • ambiguity_resolution → schema_explorer (targeted retry with user’s clarification
                            appended as feedback)

Satisfaction check sits between refiner success path and finalizer (G2-04).
"""

import logging
from agent.config import settings

logger = logging.getLogger(__name__)
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.runnables.config import RunnableConfig
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.utils.redis_publisher import publish_node_event_sync, publish_node_event
from agent.nodes.extractor import extractor_node
from agent.nodes.init_flags import init_flags_node
from agent.nodes.init_skills import init_skills_node
from agent.nodes.schema_explorer import schema_explorer_node
from agent.nodes.query_builder import query_builder_node, hitl_query_approval_node
from agent.nodes.detect_ambiguity import detect_ambiguity_node, ambiguity_resolution_node
from agent.nodes.refiner_graph import refiner_subgraph
from agent.nodes.finalizer import finalizer_node
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


def validate_config_node(
    state: AgentState, config: RunnableConfig | None = None
) -> dict:
    """
    First node after START.  Resolves scoping_mode from state (or falls back
    to the env default) and enforces strict-mode preconditions.

    Raises:
        InvalidConfigurationException: if scoping_mode='strict' and
            allowed_tables is null or empty.
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "validate_config")

    runtime_flags = state.get("runtime_flags") or {}
    mode: str = state.get("scoping_mode") or runtime_flags.get(
        "DEFAULT_TABLE_SCOPING_MODE", settings.DEFAULT_TABLE_SCOPING_MODE
    )

    if mode == "strict":
        allowed = state.get("allowed_tables")
        if not allowed:
            raise InvalidConfigurationException(
                "scoping_mode='strict' requires allowed_tables to be a non-empty list. "
                "Execution aborted to prevent unrestricted table access."
            )

    return {"scoping_mode": mode, "execution_path": ["validate_config"]}


# ── G2-02: HITL escalation node ───────────────────────────────────────────────


def hitl_escalation_node(
    state: AgentState, config: RunnableConfig | None = None
) -> dict:
    """
    Execution pauses HERE via LangGraph interrupt_before before this node runs.
    The API consumer then calls graph.update_state() to inject a corrected query
    or provide explicit guidance, rather than just clearing the state.
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

    return {
        "escalated": True,
        "execution_path": ["hitl_escalation"],
        # Clear out error and escalation state so the resumed run starts fresh
        "escalation_reason": None,
        "rejection_category": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
        "trino_error": None,
        "error_history": [],
        "refinement_count": 0,
    }


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
        logger.warning(f"Rejection router structured output failed: {e}. Falling back to query_builder.")
        # Graceful degradation instead of raising RuntimeError
        route = "query_builder"
    return {
        "feedback_route": route,
        "raw_data_ref": None,
        "trino_error": None,
        "execution_path": ["rejection_router"],
    }


# ── Conditional edge functions ────────────────────────────────────────────────




def route_refiner_subagent(state: AgentState) -> str:
    """G2-02, G2-04: Route out of refiner subagent based on escalated state or failures."""
    if state.get("escalation_reason"):
        return "hitl_escalation"
    if state.get("satisfaction_failures"):
        fail_count = state.get("satisfaction_fail_count") or 0
        if fail_count >= settings.SATISFACTION_MAX_FAILURES:
            return "hitl_escalation"
    if state.get("trino_error"):
        # If it exited the subgraph and still has a trino error, it hit the max iterations limit
        return "hitl_escalation"
    return "finalizer"


def route_detect_ambiguity(state: AgentState) -> str:
    """
    Route out of detect_ambiguity based on the resolved ambiguity_type.

      - "clear"        → hitl_query_approval    (proceed normally)
      - "ambiguous"    → ambiguity_resolution   (HITL: user clarifies, then retry)
                         → END if MAX_AMBIGUITY_RETRIES exhausted
      - "unanswerable" → END                    (data doesn’t exist; clarification won’t help)
    """
    t = state.get("ambiguity_type") or "clear"

    if t == "clear":
        return "hitl_query_approval"

    if t == "unanswerable":
        return END

    # ambiguous — offer HITL (max retries logic is now handled inside detect_ambiguity_node)
    return "ambiguity_resolution"


def route_query_builder(state: AgentState) -> str:
    runtime_flags = state.get("runtime_flags") or {}
    enable_ambiguity = runtime_flags.get("SCHEMA_AMBIGUITY_DETECT", settings.ENABLE_AMBIGUITY_DETECT)
    if isinstance(enable_ambiguity, str):
        enable_ambiguity = enable_ambiguity.lower() == "true"
        
    if enable_ambiguity:
        return "detect_ambiguity"
    return "hitl_query_approval"


def route_hitl_approval(state: AgentState) -> str:
    if state.get("feedback"):
        return "rejection_router"
    return "refiner_subagent"


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
workflow.add_node("detect_ambiguity", detect_ambiguity_node)
workflow.add_node("ambiguity_resolution", ambiguity_resolution_node)
workflow.add_node("hitl_query_approval", hitl_query_approval_node)
workflow.add_node("rejection_router", rejection_router_node)
workflow.add_node("refiner_subagent", refiner_subgraph)
workflow.add_node("hitl_escalation", hitl_escalation_node)
workflow.add_node("finalizer", finalizer_node)

# Entry: resolve flags → validate config → load skills → start reasoning
workflow.add_edge(START, "init_flags")
workflow.add_edge("init_flags", "validate_config")
workflow.add_edge("init_flags", "init_skills")
workflow.add_edge("init_skills", "extractor")
workflow.add_edge("extractor", "schema_explorer")
workflow.add_edge("schema_explorer", "query_builder")

workflow.add_conditional_edges(
    "query_builder",
    route_query_builder,
    {
        "detect_ambiguity": "detect_ambiguity",
        "hitl_query_approval": "hitl_query_approval",
    },
)

workflow.add_conditional_edges(
    "detect_ambiguity",
    route_detect_ambiguity,
    {
        "hitl_query_approval": "hitl_query_approval",
        "ambiguity_resolution": "ambiguity_resolution",
        END: END,
    },
)

# ambiguity_resolution → query_builder: targeted retry.
# The user’s clarification is in state["feedback"] which query_builder reads.
workflow.add_edge("ambiguity_resolution", "query_builder")

workflow.add_conditional_edges(
    "hitl_query_approval",
    route_hitl_approval,
    {"rejection_router": "rejection_router", "refiner_subagent": "refiner_subagent"},
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
    "refiner_subagent",
    route_refiner_subagent,
    {
        "finalizer": "finalizer",
        "hitl_escalation": "hitl_escalation",
    },
)

# G2-02: HITL resume path → restart from extractor (full state reset by human)
workflow.add_edge("hitl_escalation", "extractor")
workflow.add_edge("finalizer", END)

memory = MemorySaver()
agent_graph = workflow.compile(
    checkpointer=memory,
    interrupt_before=[
        "hitl_escalation",      # G2-02: hard error escalation (full reset)
        "ambiguity_resolution", # Ambiguity HITL: user clarifies, then schema_explorer retry
    ],
)
