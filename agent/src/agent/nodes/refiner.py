import json
import uuid
from agent.state import AgentState
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from core import execute_query_sync
from esca_sdk import EscaClient
from agent.config import settings
from agent.langfuse_client import langfuse_client

llm = ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, temperature=0)

MAX_REFINER_ITERATIONS = 3

async def refiner_node(state: AgentState):
    """Refine SQL against Trino."""
    sql = state.get("sql_query")
    count = state.get("refinement_count", 0)

    # Execute against Trino
    result = execute_query_sync(sql)

    if not result.success:
        trino_error = result.error_message or "Unknown Trino error"
        
        # Increment Prometheus refiner iteration count
        try:
            from core.metrics import refiner_iterations_total
            refiner_iterations_total.inc()
        except Exception as e:
            pass

        # If we reached the refinement limit, just stop and don't prompt LLM
        if count >= MAX_REFINER_ITERATIONS:
            try:
                from core.metrics import refiner_max_loop_fallbacks_total
                refiner_max_loop_fallbacks_total.inc()
            except Exception as e:
                pass
            return {"trino_error": trino_error, "refinement_count": count + 1}

        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_REFINER)
        prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
        chain = prompt | llm
        response = await chain.ainvoke({"sql": sql, "error": trino_error})
        new_sql = response.content.replace('```sql', '').replace('```', '').strip()
        if new_sql.endswith(';'):
            new_sql = new_sql[:-1].strip()
        return {"sql_query": new_sql, "trino_error": trino_error, "refinement_count": count + 1}
    else:
        # Log successful query execution to Splunk HEC
        try:
            from core.splunk import splunk_log
            from structlog.contextvars import get_contextvars
            ctx = get_contextvars()
            event_data = {
                "session_id": ctx.get("session_id"),
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "final_sql": sql,
                "refiner_iterations": count,
                "execution_duration_ms": result.execution_time_ms,
                "langfuse_trace_id": ctx.get("langfuse_trace_id")
            }
            await splunk_log(event_data, "query_execution")
        except Exception as e:
            pass

        # Success, save payload via Esca
        client = EscaClient(api_key=settings.ESCA_API_KEY, base_url=settings.ESCA_URL)
        payload_data = {
            "columns": result.columns,
            "rows": result.rows
        }
        payload = json.dumps(payload_data).encode()
        try:
            res = await client.save_data(payload)
            raw_ref = res.get("esca_id")
        except Exception as esca_exc:
            try:
                from core.metrics import esca_write_failures_total
                esca_write_failures_total.labels(failure_type=type(esca_exc).__name__).inc()
            except:
                pass
            
            try:
                from core.splunk import splunk_log
                from structlog.contextvars import get_contextvars
                ctx = get_contextvars()
                await splunk_log({
                    "failure_type": type(esca_exc).__name__,
                    "error_message": str(esca_exc),
                    "session_id": ctx.get("session_id"),
                    "request_id": ctx.get("request_id")
                }, "esca_failure")
            except:
                pass
            raise esca_exc
        finally:
            await client.close()

        return {"trino_error": None, "raw_data_ref": raw_ref}

