import json
import asyncio
import logging
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from core.trino import execute_query_sync
from agent.config import settings
from agent.langfuse_client import langfuse_client
from langchain_core.prompts import ChatPromptTemplate
from agent.llm import get_llm
from agent.utils.sql import clean_sql
from agent.utils.esca import get_esca_client
import datetime

llm = get_llm("refiner")


async def refiner_node(state: AgentState, config: RunnableConfig | None = None):
    """Refine SQL against Trino."""
    sql = state.get("sql_query")
    count = state.get("refinement_count", 0)
    error_history = state.get("error_history") or []
    runtime_flags = state.get("runtime_flags") or {}
    execution_path = state.get("execution_path") or []

    # Resolve per-invocation limit (DS-tunable via flags)
    max_iterations = int(runtime_flags.get("MAX_REFINER_ITERATIONS", settings.MAX_REFINER_ITERATIONS))

    # Check if we were routed here due to satisfaction check failures
    satisfaction_failures = state.get("satisfaction_failures")

    if satisfaction_failures:
        success = False
        trino_error = "; ".join(satisfaction_failures)
        error_history.append(f"Satisfaction Check Failed: {trino_error}")
        result = None
        # Clear satisfaction failures so next pass can execute cleanly
        # Note: LangGraph state updates require explicitly passing None or handling it if merging
    else:
        # Execute against Trino
        try:
            result = await asyncio.to_thread(execute_query_sync, sql)
            success = result.success
            trino_error = result.error_message or "Unknown Trino error"
            if not success:
                error_history.append(trino_error)
        except Exception as e:
            success = False
            trino_error = str(e)
            error_history.append(trino_error)
            result = None

    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    await publish_node_event(thread_id, "refiner")

    if not success:
        # If we reached the refinement limit, just stop and don't prompt LLM
        if count >= max_iterations:
            return {
                "trino_error": trino_error,
                "last_error": trino_error,
                "refinement_count": count + 1,
                "error_history": error_history,
                "escalation_reason": (
                    f"Refiner exhausted {max_iterations} iterations. "
                    f"Last Trino error: {trino_error}"
                ),
                "execution_path": execution_path + ["refiner"],
            }

        try:
            langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_REFINER)
        except Exception as prompt_err:
            logging.getLogger(__name__).warning(f"Failed to get refiner prompt from Langfuse: {prompt_err}. Using fallback.")
            langfuse_prompt = None

        if langfuse_prompt is not None:
            prompt = ChatPromptTemplate.from_messages(
                langfuse_prompt.get_langchain_prompt()
            )
        else:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a Trino SQL expert database assistant. Your task is to fix a Trino SQL query that failed with a syntax or schema error."),
                ("user", "Original User Query: {user_query}\n\nFailed SQL Query: {sql}\n\nTrino Error: {error}\n\nError History: {error_history}\n\nDatabase Schema Context:\n{schema_context}\n\nPlease rewrite the SQL query to fix the error. Return ONLY the valid SQL query inside a ```sql ``` block.")
            ])
        _llm = get_llm("refiner", runtime_flags=runtime_flags)
        chain = prompt | _llm

        schema_context = build_refiner_schema_context(state)

        if langfuse_client and langfuse_client.get_current_trace_id():
            langfuse_client._create_trace_tags_via_ingestion(
                trace_id=langfuse_client.get_current_trace_id(),
                tags=["schema_context_injected=True"],
            )

        llm_response = await chain.ainvoke(
            {
                "sql": sql,
                "error": trino_error,
                "schema_context": schema_context,
                "error_history": json.dumps(error_history),
                "user_query": state.get("user_query", ""),
            }
        )
        new_sql = clean_sql(llm_response.content)
        return {
            "sql_query": new_sql,
            "trino_error": trino_error,
            "last_error": trino_error,
            "refinement_count": count + 1,
            "error_history": error_history,
            "satisfaction_failures": None,  # clear them so we don't loop immediately
            "execution_path": execution_path + ["refiner"],
        }
    else:
        # Success, save payload via Esca
        raw_ref = None
        esca_write_failed = False
        inline_result_rows = result.rows
        inline_result_columns = result.columns

        esca_write_enabled = str(runtime_flags.get("ESCA_WRITE_ENABLED", settings.ESCA_WRITE_ENABLED)).lower() == "true"
        
        def json_serial(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.isoformat()
            raise TypeError("Type %s not serializable" % type(obj))

        if esca_write_enabled:
            try:
                payload_data = {"columns": result.columns, "rows": result.rows}
                payload = json.dumps(payload_data, default=json_serial).encode()
                async with get_esca_client() as client:
                    res = await client.save_data(payload)
                    raw_ref = res.get("esca_id")
            except Exception as e:
                esca_write_failed = True
                error_msg = f"ESCA write failed: {e}"
                if langfuse_client and langfuse_client.get_current_trace_id():
                    langfuse_client.update_current_span(
                        level="WARNING", status_message=error_msg
                    )
                logging.warning(error_msg)
                # ESCA is an optional output store — do not crash the agent.
                # The query result is still available as inline_result_rows/columns.

        return {
            "trino_error": None,
            "last_error": None,
            "raw_data_ref": raw_ref,
            "esca_write_failed": esca_write_failed,
            "inline_result_rows": inline_result_rows,
            "inline_result_columns": inline_result_columns,
            "error_history": error_history,
            "execution_path": execution_path + ["refiner"],
        }

def build_refiner_schema_context(state: AgentState) -> str:
    """Build schema context for the refiner, preferring enriched table
    profiles over the raw schema plan when available."""
    table_profiles = state.get("table_profiles")
    if table_profiles:
        # Rich, structured schema info (columns, notes, assumptions, constraints)
        return json.dumps(table_profiles, ensure_ascii=False, indent=2)

    schema_plan = state.get("schema_plan")
    if schema_plan:
        # Fallback: flat schema/plan string used by the composer
        return schema_plan

    return "No schema context available."