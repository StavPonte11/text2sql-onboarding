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
from agent.services.enrichment_orchestrator import EnrichmentOrchestrator
from agent.services.enrichment_models import AgentSQLTable

logger = logging.getLogger(__name__)


def build_refiner_schema_context(state: AgentState) -> str:
    catalog = state.get("jeen_catalog")
    if catalog:
        return catalog

    table_profiles = state.get("table_profiles") or []
    if table_profiles:
        runtime_flags = state.get("runtime_flags") or {}
        max_tables = runtime_flags.get("REFINER_SCHEMA_CONTEXT_TABLES")
        if max_tables and isinstance(max_tables, int):
            table_profiles = table_profiles[:max_tables]
        return json.dumps(table_profiles, indent=2)

    return "No schema context available."


async def enrich_context_node(state: AgentState, config: RunnableConfig | None = None):
    """Entry point: enriches the query."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    await publish_node_event(thread_id, "enrich_context")
    execution_path = state.get("execution_path") or []

    sql = state.get("sql_query")
    table_profiles = state.get("table_profiles")
    if table_profiles and sql:
        try:
            schema = {}
            tables = []
            for p in table_profiles:
                t_name = p.get("table_name", "")
                if not t_name:
                    continue
                columns_schema = {}
                columns_meta = {}
                for col in p.get("columns", []):
                    c_name = col.get("name", "")
                    sem_type = col.get("semantic_type", "unknown")
                    columns_schema[c_name] = sem_type
                    columns_meta[c_name] = {"column_type": sem_type}
                schema[t_name] = columns_schema
                tables.append(
                    AgentSQLTable(
                        name=t_name,
                        description=p.get("description", ""),
                        columns=columns_meta,
                    )
                )

            refined_sql, _, enriched = await EnrichmentOrchestrator.enrich_query(
                user_request=state.get("user_query"),
                initial_sql=sql,
                schema=schema,
                tables=tables,
            )
            if enriched and refined_sql:
                logger.info(
                    "Category Enrichment successfully refined query filters in refiner."
                )
                sql = refined_sql
        except Exception as e:
            logger.error(
                f"Category Enrichment failed in enrich_context_node: {e}", exc_info=True
            )

    return {"sql_query": sql, "execution_path": ["enrich_context"]}


async def agent_node(state: AgentState, config: RunnableConfig | None = None):
    """Central LLM reasoning node."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    await publish_node_event(thread_id, "agent")
    execution_path = state.get("execution_path") or []

    count = state.get("refinement_count", 0)
    runtime_flags = state.get("runtime_flags") or {}
    max_iterations = int(
        runtime_flags.get("MAX_REFINER_ITERATIONS", settings.MAX_REFINER_ITERATIONS)
    )

    prev_node = execution_path[-1] if execution_path else None

    # We no longer short-circuit; we let the LLM execute step 1 or step 2.
    is_step_1 = prev_node == "enrich_context" or count == 0

    trino_error = state.get("trino_error") or ""
    satisfaction_failures = state.get("satisfaction_failures")

    error_msg = trino_error
    if satisfaction_failures:
        error_msg = "Satisfaction Check Failed: " + "; ".join(satisfaction_failures)

    error_history = state.get("error_history") or []

    if count >= max_iterations:
        return {
            "escalation_reason": f"Refiner exhausted {max_iterations} iterations. Last error: {error_msg}",
            "execution_path": ["agent"],
        }

    prompt_key = (
        settings.LANGFUSE_PROMPT_REFINER_STEP1
        if is_step_1
        else settings.LANGFUSE_PROMPT_REFINER_STEP2
    )
    try:
        langfuse_prompt = langfuse_client.get_prompt(prompt_key)
        prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
    except Exception as e:
        logger.warning(f"Could not fetch prompt '{prompt_key}' from Langfuse: {e}. Trying base refiner prompt.")
        fallback_key = settings.LANGFUSE_PROMPT_REFINER
        try:
            langfuse_prompt = langfuse_client.get_prompt(fallback_key)
            prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
        except Exception as e2:
            logger.error(f"Failed to load any refiner prompt from Langfuse: {e2}")
            raise RuntimeError(f"Could not load refiner prompts from Langfuse: {e2}") from e2

    _llm = get_llm("refiner", runtime_flags=runtime_flags)
    chain = prompt | _llm

    schema_context = build_refiner_schema_context(state)

    import datetime

    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Inject enrichments into the context instruction
    enrichments = state.get("query_enrichments")
    enriched_instruction = ""
    if enrichments:
        enriched_instruction = (
            f"[QUERY ENRICHMENTS]\n{json.dumps(enrichments, indent=2)}"
        )

    if langfuse_client and langfuse_client.get_current_trace_id():
        langfuse_client._create_trace_tags_via_ingestion(
            trace_id=langfuse_client.get_current_trace_id(),
            tags=["schema_context_injected=True", f"step={'1' if is_step_1 else '2'}"],
        )

    # Prepare variables matching the new human prompts
    invoke_vars = {
        "schema": schema_context,
        "user_request": state.get("user_query") or "",
        "location_wkt_instruction": state.get("location_wkt_instruction") or "",
        "current_time": current_time,
        "initial_query": state.get("sql_query") or "",
        "current_agent_query": state.get("sql_query") or "",
        "enriched_instruction": enriched_instruction,
        "last_result_success": "True" if not trino_error else "False",
        "last_result_error": error_msg,
        "last_result_row_count": state.get("last_result_row_count", ""),
        "last_result_data": state.get("last_result_data", ""),
    }

    response = await chain.ainvoke(invoke_vars)
    new_sql = clean_sql(response.content)

    import re

    is_satisfied = "QUERY_SATISFIED" in response.content
    sql_explanation = state.get("sql_explanation", "")

    if is_satisfied:
        match = re.search(
            r"TRANSLATION\s*:?\s*\n*(.*)", response.content, re.IGNORECASE | re.DOTALL
        )
        if match:
            sql_explanation = match.group(1).strip()

    return {
        "sql_query": new_sql,
        "refinement_count": count + 1,
        "satisfaction_failures": None,
        "is_satisfied": is_satisfied,
        "sql_explanation": sql_explanation,
        "execution_path": ["agent"],
    }


async def trino_exec_node(state: AgentState, config: RunnableConfig | None = None):
    """Executes query against Trino."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    await publish_node_event(thread_id, "trino_exec")
    error_history = state.get("error_history") or []
    sql = state.get("sql_query")
    runtime_flags = state.get("runtime_flags") or {}
    import re

    # ── Map WKT placeholders and short table names before Trino execution ──
    # 1. WKT Polygons
    locations_dict = state.get("locations_dict")
    if locations_dict and "coords" in locations_dict:
        for placeholder, wkt_str in locations_dict["coords"].items():
            # The prompt instructs the LLM to use @<placeholder>@
            sql = re.sub(r"@" + re.escape(placeholder) + r"@", f"'{wkt_str}'", sql)

    # 2. Short Table Names -> Fully Qualified Names
    table_mappings: dict[str, str] = {}
    table_profiles = state.get("table_profiles") or []
    for p in table_profiles:
        s_name = p.get("table_name")
        f_name = p.get("full_name")
        if s_name and f_name:
            table_mappings[s_name.strip('"')] = f_name

    jeen_catalog = state.get("jeen_catalog") or ""
    if jeen_catalog:
        pattern = r'\"([^\"]+)\"\.\"([^\"]+)\"\.\"([^\"]+)\"'
        for cat, sch, tbl in re.findall(pattern, jeen_catalog):
            if cat.lower() != "catalog" and tbl.lower() != "table":
                full_name = f'"{cat}"."{sch}"."{tbl}"'
                table_mappings[tbl] = full_name

    for short, full in table_mappings.items():
        match = re.match(r'\"([^\"]+)\"\.\"([^\"]+)\"\.\"([^\"]+)\"', full)
        if match:
            cat, sch, tbl = match.groups()
            sql = re.sub(rf'(?<![\.\w\"])\"{re.escape(sch)}\"\.\"{re.escape(tbl)}\"', full, sql)
            sql = re.sub(rf'(?<![\.\w\"]){re.escape(sch)}\.{re.escape(tbl)}(?![\.\w\"])', full, sql)
        sql = re.sub(rf'(?<![\.\w])\"{re.escape(short)}\"(?![\.\w])', full, sql)
        sql = re.sub(rf'(?<![\.\w\"]){re.escape(short)}(?![\.\w\"])', full, sql)

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
        return {
            "sql_query": sql,
            "trino_error": trino_error,
            "last_error": trino_error,
            "error_history": error_history,
            "execution_path": ["trino_exec"],
            "last_result_row_count": None,
            "last_result_data": None,
        }
    else:
        raw_ref = None
        esca_write_failed = False
        inline_result_rows = result.rows
        inline_result_columns = result.columns

        esca_write_enabled = (
            str(
                runtime_flags.get("ESCA_WRITE_ENABLED", settings.ESCA_WRITE_ENABLED)
            ).lower()
            == "true"
        )

        if esca_write_enabled:
            try:
                payload_data = {"columns": result.columns, "rows": result.rows}
                payload = json.dumps(payload_data, default=str).encode()
                async with get_esca_client() as client:
                    res = await client.save_data(payload)
                    raw_ref = res.get("esca_id")
            except Exception as e:
                esca_write_failed = True
                if langfuse_client and langfuse_client.get_current_trace_id():
                    langfuse_client.update_current_span(
                        level="ERROR", status_message=f"ESCA write failed: {e}"
                    )
                else:
                    logging.error(f"ESCA write failed: {e}")
                raise RuntimeError(f"Failed to write query result to ESCA: {e}")

        return {
            "sql_query": sql,
            "trino_error": None,
            "last_error": None,
            "raw_data_ref": raw_ref,
            "esca_write_failed": esca_write_failed,
            "inline_result_rows": inline_result_rows,
            "inline_result_columns": inline_result_columns,
            "error_history": error_history,
            "execution_path": ["trino_exec"],
            "last_result_row_count": len(inline_result_rows)
            if inline_result_rows
            else 0,
            "last_result_data": str([inline_result_columns] + inline_result_rows[:5])
            if inline_result_rows
            else "[]",
        }
