import json
import uuid
import asyncio
import logging
from agent.state import AgentState
from core import execute_query_sync
from agent.config import settings
from agent.langfuse_client import langfuse_client
from langfuse.decorators import langfuse_context
from langchain_core.prompts import ChatPromptTemplate
from agent.llm import get_llm
from agent.utils.sql import clean_sql
from agent.utils.esca import get_esca_client

llm = get_llm("refiner")

MAX_REFINER_ITERATIONS = 3
REFINER_SCHEMA_CONTEXT_TABLES = 4

def build_refiner_schema_context(state: AgentState) -> str:
    profiles = state.get("table_profiles")
    if not profiles:
        return "No schema context available."
    
    # Cap the context to REFINER_SCHEMA_CONTEXT_TABLES
    capped_profiles = profiles[:REFINER_SCHEMA_CONTEXT_TABLES]
    return json.dumps(capped_profiles, indent=2)

async def refiner_node(state: AgentState):
    """Refine SQL against Trino."""
    sql = state.get("sql_query")
    count = state.get("refinement_count", 0)
    error_history = state.get("error_history") or []

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

    if not success:
        # If we reached the refinement limit, just stop and don't prompt LLM
        if count >= MAX_REFINER_ITERATIONS:
            return {"trino_error": trino_error, "last_error": trino_error, "refinement_count": count + 1, "error_history": error_history}

        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_REFINER)
        prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
        chain = prompt | llm
        
        schema_context = build_refiner_schema_context(state)
        
        if langfuse_context.get_current_trace_id():
            langfuse_context.update_current_trace(tags=["schema_context_injected=True"])
            
        response = await chain.ainvoke({
            "sql": sql, 
            "error": trino_error,
            "schema_context": schema_context,
            "error_history": json.dumps(error_history)
        })
        new_sql = clean_sql(response.content)
        return {"sql_query": new_sql, "trino_error": trino_error, "last_error": trino_error, "refinement_count": count + 1, "error_history": error_history}
    else:
        # Success, save payload via Esca
        payload_data = {
            "columns": result.columns,
            "rows": result.rows
        }
        payload = json.dumps(payload_data).encode()
        raw_ref = None
        esca_write_failed = False
        inline_result_rows = result.rows
        
        async with get_esca_client() as client:
            try:
                res = await client.save_data(payload)
                raw_ref = res.get("esca_id")
            except Exception as e:
                esca_write_failed = True
                if langfuse_context.get_current_trace_id():
                    langfuse_context.update_current_observation(level="WARNING", status_message=f"ESCA write failed: {e}")
                else:
                    logging.warning(f"ESCA write failed: {e}")

        return {
            "trino_error": None, 
            "last_error": None,
            "raw_data_ref": raw_ref, 
            "esca_write_failed": esca_write_failed, 
            "inline_result_rows": inline_result_rows,
            "error_history": error_history
        }

