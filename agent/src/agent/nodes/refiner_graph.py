import logging
from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.config import settings
from agent.nodes.refiner import refiner_node
from agent.nodes.satisfaction_check import satisfaction_check_node

logger = logging.getLogger(__name__)

def route_refiner_subgraph(state: AgentState) -> str:
    """G2-02: Route from refiner to satisfaction check or exit."""
    runtime_flags = state.get("runtime_flags") or {}
    max_iterations = int(runtime_flags.get("MAX_REFINER_ITERATIONS", settings.MAX_REFINER_ITERATIONS))
    
    if state.get("trino_error"):
        if state.get("refinement_count", 0) >= max_iterations:
            return END
            
    # Issue 37: check SATISFACTION_CHECK_ENABLED in router
    check_enabled = runtime_flags.get("SATISFACTION_CHECK_ENABLED", settings.SATISFACTION_CHECK_ENABLED)
    # Convert check_enabled to boolean properly if it's a string
    if isinstance(check_enabled, str):
        check_enabled = check_enabled.lower() == "true"
    if not check_enabled:
        return END

    return "satisfaction_check"


def route_satisfaction_subgraph(state: AgentState) -> str:
    """
    G2-04: Route based on satisfaction check outcome.
      - no failures  → exit (success)
      - failures, under MAX → refiner (loop)
      - failures, over MAX  → exit (escalation)
    """
    failures = state.get("satisfaction_failures")
    if not failures:
        return END

    fail_count = state.get("satisfaction_fail_count") or 0
    if fail_count >= settings.SATISFACTION_MAX_FAILURES:
        return END
        
    return "refiner"


# ── Build Subgraph ────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("refiner", refiner_node)
workflow.add_node("satisfaction_check", satisfaction_check_node)

workflow.add_edge(START, "refiner")

workflow.add_conditional_edges(
    "refiner",
    route_refiner_subgraph,
    {
        "satisfaction_check": "satisfaction_check",
        END: END,
    },
)

workflow.add_conditional_edges(
    "satisfaction_check",
    route_satisfaction_subgraph,
    {
        "refiner": "refiner",
        END: END,
    },
)

# Compile without a checkpointer, the parent graph handles memory
refiner_subgraph = workflow.compile()
