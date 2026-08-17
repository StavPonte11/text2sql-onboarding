import json
import logging

logger = logging.getLogger(__name__)
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm
from agent.utils.esca import get_esca_client


async def get_esca_preview(esca_id: str, limit: int | None = None) -> str:
    """Load data from Esca and return a preview of the columns and the rows."""
    if not esca_id:
        return "No data reference found."

    try:
        async with get_esca_client() as client:
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
            return json.dumps(preview_info, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error retrieving data preview from Esca: {e}")
        return "Data preview is currently unavailable."


async def finalizer_node(state: AgentState, config: RunnableConfig | None = None):
    """Summarize data using the unified Hebrew finalizer prompt."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    await publish_node_event(thread_id, "finalizer")

    raw_data_ref = state.get("raw_data_ref")
    inline_result_rows = state.get("inline_result_rows")
    inline_result_columns = state.get("inline_result_columns")
    runtime_flags = state.get("runtime_flags") or {}
    llm = get_llm("finalizer", runtime_flags=runtime_flags)

    esca_write_enabled = (
        str(
            runtime_flags.get("ESCA_WRITE_ENABLED", settings.ESCA_WRITE_ENABLED)
        ).lower()
        == "true"
    )

    raw_limit = runtime_flags.get("PREVIEW_ROWS_COUNT", settings.PREVIEW_ROWS_COUNT)
    limit = int(raw_limit) if raw_limit is not None else None
    preview_str = ""
    if not esca_write_enabled or not raw_data_ref:
        if inline_result_rows is not None:
            preview_rows = inline_result_rows[:limit] if limit is not None else inline_result_rows
            if inline_result_columns:
                columns = inline_result_columns
            elif preview_rows and isinstance(preview_rows[0], dict):
                columns = list(preview_rows[0].keys())
            else:
                columns = []
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

    prompt_name = settings.LANGFUSE_PROMPT_FINALIZER
    langfuse_prompt = langfuse_client.get_prompt(prompt_name)
    prompt_finalizer = ChatPromptTemplate.from_messages(
        langfuse_prompt.get_langchain_prompt()
    )

    chain = prompt_finalizer | llm
    response = await chain.ainvoke(
        {
            "user_request": state.get("user_query") or "",
            "sql_query": state.get("sql_query") or "",
            "sql_translation": state.get("sql_explanation") or "",
            "sql_results": preview_str,
        }
    )

    return {
        "summary": response.content,
        "sql_explanation": state.get("sql_explanation", ""),
        "execution_path": ["finalizer"],
    }
