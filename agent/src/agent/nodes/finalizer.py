import json
import asyncio
from agent.state import AgentState
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from esca_sdk import EscaClient
from agent.config import settings
from agent.langfuse_client import langfuse_client

llm = ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, temperature=0)

# Prompts will be loaded dynamically from Langfuse inside the node execution

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
        
        import datetime
        def json_serial(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.isoformat()
            raise TypeError("Type %s not serializable" % type(obj))

        preview_info = {
            "columns": columns,
            "preview_rows": preview_rows,
            "preview_count": len(preview_rows),
            "total_rows": total_rows
        }
        return json.dumps(preview_info, default=json_serial, indent=2)
    except Exception as e:
        return f"Error retrieving data preview from Esca: {e}"
    finally:
        await client.close()

async def get_sql_explanation(sql_query: str | None) -> str:
    """Ask LLM to explain the SQL query in natural language."""
    if not sql_query:
        return "No SQL query was generated."
        
    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_FINALIZER_SQL_EXPLANATION)
    prompt_sql_explanation = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
    
    chain = prompt_sql_explanation | llm
    response = await chain.ainvoke({"sql_query": sql_query})
    return response.content

async def finalizer_node(state: AgentState):
    """Summarize data."""
    raw_data_ref = state.get("raw_data_ref")
    preview_str = ""
    if raw_data_ref:
        preview_str = await get_esca_preview(raw_data_ref)
        
    langfuse_prompt_summary = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_FINALIZER_SUMMARY)
    prompt_summary = ChatPromptTemplate.from_messages(langfuse_prompt_summary.get_langchain_prompt())
        
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

