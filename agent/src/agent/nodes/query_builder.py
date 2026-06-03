from agent.state import AgentState
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from langgraph.types import interrupt

llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

def query_builder_node(state: AgentState):
    """Build SQL from plan and pause for user approval."""
    feedback = state.get("feedback")
    feedback_str = f"\nUser Feedback to apply: {feedback}" if feedback else ""

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a SQL expert who specializes in trino. Build a SQL query based on the plan and user query. Output ONLY the SQL query, nothing else."),
        ("human", "Plan: {schema_plan}\nQuery: {user_query}{feedback_str}")
    ])
    chain = prompt | llm
    response = chain.invoke({
        "schema_plan": state.get("schema_plan"), 
        "user_query": state["user_query"],
        "feedback_str": feedback_str
    })
    sql = response.content.replace('```sql', '').replace('```', '').strip()
    if sql.endswith(';'):
        sql = sql[:-1].strip()
        
    if state.get("non_interactive"):
        return {"sql_query": sql, "refinement_count": 0, "trino_error": None, "feedback": None}
        
    approval_result = interrupt({
        "type": "query_approval",
        "schema_plan": state.get("schema_plan"),
        "sql_query": sql
    })
    
    if approval_result.get("approved"):
        return {"sql_query": sql, "refinement_count": 0, "trino_error": None, "feedback": None}
    else:
        return {
            "feedback": approval_result.get("feedback", "Query rejected by user"),
            "sql_query": None
        }



