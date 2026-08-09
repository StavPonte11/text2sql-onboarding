import json
import asyncio
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm
from agent.utils.esca import get_esca_client

from agent.utils.esca import get_esca_client


async def get_esca_preview(esca_id: str, limit: int | None = None) -> str:
    """Load data from Esca and return a preview of the columns and the rows."""
    if not esca_id:
        return "No data reference found."

    try:
        async with get_esca_client() as client:
            # TODO: instead of fetching everything from esca and then chunk, get only the chunk
            data_bytes = await client.load_head(esca_id)
            data = json.loads(data_bytes.decode())

            columns = data.get("columns", [])
            rows = data.get("rows", [])
            total_rows = len(rows)

            # Take a slice of the rows to avoid context overload if limit is provided
            preview_rows = rows[:limit] if limit is not None else rows

            preview_info = {
                "columns": columns,
                "preview_rows": preview_rows,
                "preview_count": len(preview_rows),
                "total_rows": total_rows,
            }
            return json.dumps(preview_info, indent=2)
    except Exception as e:
        return f"Error retrieving data preview from Esca: {e}"


async def get_sql_explanation(sql_query: str | None, llm) -> str:
    """Ask LLM to explain the SQL query in natural language."""
    if not sql_query:
        return "No SQL query was generated."

    langfuse_prompt = langfuse_client.get_prompt(
        settings.LANGFUSE_PROMPT_FINALIZER_SQL_EXPLANATION
    )
    prompt_sql_explanation = ChatPromptTemplate.from_messages(
        langfuse_prompt.get_langchain_prompt()
    )

    chain = prompt_sql_explanation | llm
    response = await chain.ainvoke({"sql_query": sql_query})
    return response.content


async def finalizer_node(state: AgentState, config: RunnableConfig | None = None):
    """Summarize data."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    from agent.utils.redis_publisher import publish_node_event
    await publish_node_event(thread_id, "finalizer")
    raw_data_ref = state.get("raw_data_ref")
    esca_write_failed = state.get("esca_write_failed", False)
    inline_result_rows = state.get("inline_result_rows")
    runtime_flags = state.get("runtime_flags") or {}
    llm = get_llm("finalizer", runtime_flags=runtime_flags)

    esca_write_enabled = str(runtime_flags.get("ESCA_WRITE_ENABLED", settings.ESCA_WRITE_ENABLED)).lower() == "true"
    
    raw_limit = runtime_flags.get("PREVIEW_ROWS_COUNT", settings.PREVIEW_ROWS_COUNT)
    limit = int(raw_limit) if raw_limit is not None else None
    
    preview_str = ""
    if not esca_write_enabled:
        if inline_result_rows is not None:
            preview_rows = inline_result_rows[:limit] if limit is not None else inline_result_rows
            columns = (
                list(preview_rows[0].keys())
                if preview_rows and isinstance(preview_rows[0], dict)
                else []
            )
            preview_info = {
                "columns": columns,
                "preview_rows": preview_rows,
                "preview_count": len(preview_rows),
                "total_rows": len(inline_result_rows),
            }
            preview_str = json.dumps(preview_info, indent=2, default=str)
        else:
            preview_str = "No data reference found."
    else:
        preview_str = await get_esca_preview(raw_data_ref, limit=limit)

    langfuse_prompt_summary = langfuse_client.get_prompt(
        settings.LANGFUSE_PROMPT_FINALIZER_SUMMARY
    )
    prompt_summary = ChatPromptTemplate.from_messages(
        langfuse_prompt_summary.get_langchain_prompt()
    )

    summary_chain = prompt_summary | llm

    summary_task = summary_chain.ainvoke(
        {
            "user_query": state["user_query"],
            "sql_query": state.get("sql_query") or "",
            "raw_data_ref": raw_data_ref,
            "data_preview": preview_str,
        }
    )

    sql_task = get_sql_explanation(state.get("sql_query"), llm)

    summary_response, sql_explanation = await asyncio.gather(summary_task, sql_task)

    return {
        "summary": summary_response.content,
        "sql_explanation": sql_explanation,
        "execution_path": ["finalizer"],
    }
