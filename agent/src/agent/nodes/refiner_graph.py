import logging
from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.config import settings
from agent.nodes.refiner import enrich_context_node, agent_node, trino_exec_node

logger = logging.getLogger(__name__)


def route_enrich(state: AgentState) -> str:
    """Routes out of enrich_context directly to fail if query is escalated/rejected, otherwise to trino_exec."""
    if state.get("escalation_reason") or state.get("rejection_category"):
        return "fail"
    return "execute"


def route(state: AgentState) -> str:
    """
    Routes from agent node based on state and execution history.
    """
    if state.get("escalation_reason") or state.get("rejection_category"):
        return "fail"

    if state.get("is_satisfied"):
        return "success"

    return "execute"


def end_success_node(state: AgentState):
    """Terminal node representing a successfully refined and satisfied query."""
    return {}


def end_fail_node(state: AgentState):
    """Terminal node representing a failed refinement (max iterations, unanswerable, or ambiguous)."""
    reason = (
        state.get("escalation_reason")
        or state.get("rejection_category")
        or "Refiner failed."
    )
    return {"escalation_reason": reason}


# ── Build Subgraph ────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("enrich_context", enrich_context_node)
workflow.add_node("trino_exec", trino_exec_node)
workflow.add_node("agent", agent_node)
workflow.add_node("end_success", end_success_node)
workflow.add_node("end_fail", end_fail_node)

# enrich_context leads directly to initial Trino execution or early fail
workflow.add_edge(START, "enrich_context")
workflow.add_conditional_edges(
    "enrich_context",
    route_enrich,
    {
        "execute": "trino_exec",
        "fail": "end_fail",
    },
)

# trino_exec sends results to the Refiner agent for evaluation & error correction
workflow.add_edge("trino_exec", "agent")

workflow.add_conditional_edges(
    "agent",
    route,
    {
        "execute": "enrich_context",
        "success": "end_success",
        "fail": "end_fail",
    },
)

workflow.add_edge("end_success", END)
workflow.add_edge("end_fail", END)

# Compile without a checkpointer, the parent graph handles memory
refiner_subgraph = workflow.compile()

