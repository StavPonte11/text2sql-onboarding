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
    import asyncio
    try:
        result = await asyncio.to_thread(execute_query_sync, sql)
        success = result.success
        trino_error = result.error_message or "Unknown Trino error"
    except Exception as e:
        success = False
        trino_error = str(e)
        result = None

    if not success:
        # If we reached the refinement limit, just stop and don't prompt LLM
        if count >= MAX_REFINER_ITERATIONS:
            return {"trino_error": trino_error, "last_error": trino_error, "refinement_count": count + 1}

        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_REFINER)
        prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
        chain = prompt | llm
        response = await chain.ainvoke({"sql": sql, "error": trino_error})
        new_sql = response.content.replace('```sql', '').replace('```', '').strip()
        if new_sql.endswith(';'):
            new_sql = new_sql[:-1].strip()
        return {"sql_query": new_sql, "trino_error": trino_error, "last_error": trino_error, "refinement_count": count + 1}
    else:
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
        finally:
            await client.close()

        return {"trino_error": None, "raw_data_ref": raw_ref}

