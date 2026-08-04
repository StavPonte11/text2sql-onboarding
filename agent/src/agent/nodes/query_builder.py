import re
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event_sync
from agent.state import AgentState
from agent.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from agent.langfuse_client import langfuse_client
from langgraph.types import interrupt

async def query_builder_node(state: AgentState, config: RunnableConfig | None = None):
    """Build SQL from catalog and user query."""
    runtime_flags = state.get("runtime_flags") or {}
    feedback = state.get("feedback")
    feedback_str = f"\nUser Feedback to apply: {feedback}" if feedback else ""

    loaded_skills = state.get("loaded_skills")
    if loaded_skills:
        from agent.utils.skill_registry import SkillRegistry

        _skill_registry = SkillRegistry()
        skill_prompts = _skill_registry.build_system_prompt_addition(loaded_skills)
        if skill_prompts:
            feedback_str += f"\n\n[APPLIED SKILLS]{skill_prompts}"

    enrichments = state.get("query_enrichments")
    if enrichments:
        import json

        feedback_str += f"\n\n[QUERY ENRICHMENTS]\nThe user query contains ambiguous terms resolved here:\n{json.dumps(enrichments, indent=2)}"

    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_QUERY_BUILDER)
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
    _llm = get_llm("query_builder", runtime_flags=runtime_flags)
    chain = prompt | _llm
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "query_builder")
    response = await chain.ainvoke(
        {
            "jeen_catalog": state.get("jeen_catalog"),
            "user_query": state.get("user_query"),
            "feedback_str": feedback_str,
            "location_wkt_instruction": state.get("location_wkt_instruction") or "",
        }
    )
    content = response.content

    from agent.utils.sql import clean_sql

    # Check for built-in reasoning content in model metadata (additional_kwargs)
    explanation = (
        response.additional_kwargs.get("reasoning_content")
        or response.additional_kwargs.get("reasonig_content")
        or ""
    )

    sql = clean_sql(content)

    if not explanation:
        # Extract explanation by removing the SQL block (or the SQL text) from the content
        match = re.search(
            r"```(?:sql)?\s*(.*?)\s*```", content, re.IGNORECASE | re.DOTALL
        )
        if match:
            explanation = content.replace(match.group(0), "").strip()
        else:
            sql = content.strip()

    if sql.endswith(";"):
        sql = sql[:-1].strip()

    return {
        "sql_query": sql,
        "sql_explanation": explanation,
        "execution_path": ["query_builder"],
        "refinement_count": 0,
        "trino_error": None
    }

async def hitl_query_approval_node(state: AgentState, config: RunnableConfig | None = None):
    """Pause for user approval of the generated SQL."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "hitl_query_approval")

    if state.get("non_interactive"):
        return {
            "feedback": None,
            "execution_path": ["hitl_query_approval"],
        }

    approval_result = interrupt(
        {
            "type": "query_approval",
            "schema_plan": "",  # Empty string so we don't send massive catalog to UI
            "sql_query": state.get("sql_query"),
            "sql_explanation": state.get("sql_explanation"),
        }
    )

    if approval_result.get("approved"):
        return {
            "feedback": None,
            "execution_path": ["hitl_query_approval"],
        }
    else:
        return {
            "feedback": approval_result.get("feedback") or "Query rejected by user",
            "sql_query": None,
            "execution_path": ["hitl_query_approval"],
        }
