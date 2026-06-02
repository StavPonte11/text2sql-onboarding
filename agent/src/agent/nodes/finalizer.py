import json
import asyncio
from agent.state import AgentState
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from esca_sdk import EscaClient
from agent.config import settings

llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

prompt_summary = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a helpful data assistant. Summarize the findings for the user nicely. "
        "You are given the SQL query that was executed and a preview of the queried data (columns and first few rows) to help you understand the context of the results. "
        "Note: If the columns contain single items or aliases like `_col0` with a numeric value, this is the result of an aggregation query (such as `COUNT(*)`). Use this direct result to answer the user's question.\n\n"
        "Data Preview:\n{data_preview}"
    )),
    ("human", "User asked: {user_query}\nSQL Query: {sql_query}\nData Ref: {raw_data_ref}")
])

prompt_sql_explanation = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a database analyst assistant. Explain the following SQL query in clear, natural language. "
        "Describe what fields and tables are queried, any filters, joins, groupings, or aggregations, and what the query accomplishes. "
        "Keep the explanation concise and professional."
    )),
    ("human", "SQL Query:\n{sql_query}")
])

async def get_esca_preview(esca_id: str, limit: int = 5) -> str:
    """Load data from Esca and return a preview of the columns and the first few rows."""
    if not esca_id:
        return "No data reference found."
    
    client = EscaClient(api_key=settings.ESCA_API_KEY, base_url=settings.ESCA_URL)
    try:
        # TODO: instead of fetching everything from esca and then chunk, get only the chunk
        data_bytes = await client.load_head(esca_id)
        data = json.loads(data_bytes.decode())
        
        columns = data.get("columns", [])
        rows = data.get("rows", [])
        total_rows = len(rows)
        
        # Take a slice of the rows to avoid context overload
        preview_rows = rows[:limit]
        
        preview_info = {
            "columns": columns,
            "preview_rows": preview_rows,
            "preview_count": len(preview_rows),
            "total_rows": total_rows
        }
        return json.dumps(preview_info, indent=2)
    except Exception as e:
        return f"Error retrieving data preview from Esca: {e}"
    finally:
        await client.close()

async def get_sql_explanation(sql_query: str | None) -> str:
    """Ask LLM to explain the SQL query in natural language."""
    if not sql_query:
        return "No SQL query was generated."
    chain = prompt_sql_explanation | llm
    response = await chain.ainvoke({"sql_query": sql_query})
    return response.content

async def finalizer_node(state: AgentState):
    """Summarize data."""
    raw_data_ref = state.get("raw_data_ref")
    preview_str = ""
    if raw_data_ref:
        preview_str = await get_esca_preview(raw_data_ref)
        
    summary_chain = prompt_summary | llm
    
    summary_task = summary_chain.ainvoke({
        "user_query": state["user_query"],
        "sql_query": state.get("sql_query") or "",
        "raw_data_ref": raw_data_ref,
        "data_preview": preview_str
    })
    
    sql_task = get_sql_explanation(state.get("sql_query"))
    
    summary_response, sql_explanation = await asyncio.gather(summary_task, sql_task)
    
    return {
        "summary": summary_response.content,
        "sql_explanation": sql_explanation
    }

