import logging
from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.config import settings
from agent.nodes.refiner import enrich_context_node, agent_node, trino_exec_node
from agent.nodes.refiner import enrich_context_node, agent_node, trino_exec_node

logger = logging.getLogger(__name__)


def route(state: AgentState) -> str:
    """
    Consolidated routing function replacing ROUTE_AFTER_ENRICH and SHOULD_ENRICH.
    Routes from agent node based on state and execution history.
    """
    path = state.get("execution_path", [])
    if len(path) < 2:
        return "execute"  # Fallback if agent was the first node for some reason

    prev_node = path[-2]  # Node before agent

    if state.get("escalation_reason") or state.get("rejection_category"):
        return "fail"

    if prev_node == "enrich_context":
        # Query was enriched (or passed through), now test it with Trino tool
        return "execute"

    if prev_node == "trino_exec":
        # We just came from executing Trino
        if state.get("trino_error"):
            # Execution failed. Agent node was run and called the LLM to fix it.
            # We have a new SQL, send it straight to execution to test the fix!
            return "execute"
        else:
            # Execution succeeded! Agent passed through and analyzed the result.
            if state.get("is_satisfied"):
                return "success"
            else:
                return "needs_enrich"

    return "fail"


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
workflow.add_node("agent", agent_node)
workflow.add_node("trino_exec", trino_exec_node)
workflow.add_node("end_success", end_success_node)
workflow.add_node("end_fail", end_fail_node)

# enrich_context becomes a pure entry node
workflow.add_edge(START, "enrich_context")
workflow.add_edge("enrich_context", "agent")

workflow.add_conditional_edges(
    "agent",
    route,
    {
        "needs_enrich": "enrich_context",
        "execute": "trino_exec",
        "success": "end_success",
        "fail": "end_fail",
    },
)

# trino_exec appears exactly once
workflow.add_edge("trino_exec", "agent")

workflow.add_edge("end_success", END)
workflow.add_edge("end_fail", END)

# Compile without a checkpointer, the parent graph handles memory
refiner_subgraph = workflow.compile()
