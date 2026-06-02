import json
from agent.state import AgentState
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from esca_sdk import EscaClient
from agent.config import settings

llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

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

async def finalizer_node(state: AgentState):
    """Summarize data."""
    raw_data_ref = state.get("raw_data_ref")
    preview_str = ""
    if raw_data_ref:
        preview_str = await get_esca_preview(raw_data_ref)
        
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a helpful data assistant. Summarize the findings for the user nicely. "
            "You are given a preview of the queried data from Esca (columns and first few rows). "
            "Note: If the columns contain single items or aliases like `_col0` with a numeric value, this is the result of an aggregation query (such as `COUNT(*)`). Use this direct result to answer the user's question.\n\n"
            "Data Preview:\n{data_preview}"
        )),
        ("human", "User asked: {user_query}\nData Ref: {raw_data_ref}")
    ])
    chain = prompt | llm
    response = await chain.ainvoke({
        "user_query": state["user_query"],
        "raw_data_ref": raw_data_ref,
        "data_preview": preview_str
    })
    return {"summary": response.content}

