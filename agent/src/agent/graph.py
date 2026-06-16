from agent.nodes.refiner import MAX_REFINER_ITERATIONS
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.nodes.extractor import extractor_node
from agent.nodes.schema_explorer import schema_explorer_node
from agent.nodes.query_builder import query_builder_node
from agent.nodes.refiner import refiner_node
from agent.nodes.finalizer import finalizer_node
from agent.config import settings
from agent.langfuse_client import langfuse_client
from pydantic import BaseModel, Field
from typing import Literal

# Initialize LLM for rejection routing classification
llm = ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, temperature=0)

class RejectionRoute(BaseModel):
    route: Literal["extractor", "schema_explorer", "query_builder"] = Field(
        description="The phase to route the execution back to based on the user feedback."
    )

def rejection_router_node(state: AgentState):
    """Classify user rejection feedback and select the appropriate phase to return to."""
    feedback = state.get("feedback")
    if not feedback:
        return {"feedback_route": "query_builder"}
        
    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_REJECTION_ROUTER)
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
        "trino_error": None
    }


def route_refiner(state: AgentState):
    if state.get("trino_error") and state.get("refinement_count", 0) < MAX_REFINER_ITERATIONS:
        return "refiner"
    return "finalizer"

def route_query_builder(state: AgentState):
    if state.get("feedback"):
        return "rejection_router"
    return "refiner"

def route_rejection(state: AgentState):
    route = state.get("feedback_route")
    if route in ["extractor", "schema_explorer", "query_builder"]:
        return route
    return "extractor"

workflow = StateGraph(AgentState)

workflow.add_node("extractor", extractor_node)
workflow.add_node("schema_explorer", schema_explorer_node)
workflow.add_node("query_builder", query_builder_node)
workflow.add_node("rejection_router", rejection_router_node)
workflow.add_node("refiner", refiner_node)
workflow.add_node("finalizer", finalizer_node)

workflow.add_edge(START, "extractor")
workflow.add_edge("extractor", "schema_explorer")
def route_schema_explorer(state: AgentState):
    if state.get("hallucinated_tables"):
        if state.get("schema_explorer_retry_count", 0) >= 3:
            return "query_builder"
        return "schema_explorer"
    return "query_builder"

workflow.add_conditional_edges("schema_explorer", route_schema_explorer, {"schema_explorer": "schema_explorer", "query_builder": "query_builder"})

workflow.add_conditional_edges("query_builder", route_query_builder, {"rejection_router": "rejection_router", "refiner": "refiner"})
workflow.add_conditional_edges("rejection_router", route_rejection, {"extractor": "extractor", "schema_explorer": "schema_explorer", "query_builder": "query_builder"})

workflow.add_conditional_edges("refiner", route_refiner, {"refiner": "refiner", "finalizer": "finalizer"})
workflow.add_edge("finalizer", END)

memory = MemorySaver()
agent_graph = workflow.compile(checkpointer=memory)

