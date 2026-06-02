from agent.state import AgentState
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

from agent.config import settings

llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

def query_builder_node(state: AgentState):
    """Build SQL from plan."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a SQL expert who specializes in trino. Build a SQL query based on the plan and user query. Output ONLY the SQL query, nothing else."),
        ("human", "Plan: {schema_plan}\nQuery: {user_query}")
    ])
    chain = prompt | llm
    response = chain.invoke({"schema_plan": state.get("schema_plan"), "user_query": state["user_query"]})
    sql = response.content.replace('```sql', '').replace('```', '').strip()
    if sql.endswith(';'):
        sql = sql[:-1].strip()
    return {"sql_query": sql, "refinement_count": 0, "trino_error": None}

