from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.extractor import extractor_node
from agent.nodes.schema_explorer import schema_explorer_node
from agent.nodes.query_builder import query_builder_node
from agent.nodes.refiner import refiner_node
from agent.nodes.finalizer import finalizer_node

def route_refiner(state: AgentState):
    if state.get("trino_error") and state.get("refinement_count", 0) < 3:
        return "refiner"
    return "finalizer"

workflow = StateGraph(AgentState)

workflow.add_node("extractor", extractor_node)
workflow.add_node("schema_explorer", schema_explorer_node)
workflow.add_node("query_builder", query_builder_node)
workflow.add_node("refiner", refiner_node)
workflow.add_node("finalizer", finalizer_node)

workflow.add_edge(START, "extractor")
workflow.add_edge("extractor", "schema_explorer")
workflow.add_edge("schema_explorer", "query_builder")
workflow.add_edge("query_builder", "refiner")

workflow.add_conditional_edges("refiner", route_refiner, {"refiner": "refiner", "finalizer": "finalizer"})
workflow.add_edge("finalizer", END)

agent_graph = workflow.compile()
