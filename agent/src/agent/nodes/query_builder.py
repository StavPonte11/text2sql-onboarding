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
    """Build SQL from plan and pause for user approval."""
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

    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_QUERY_BUILDER)
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
    _llm = get_llm("query_builder", runtime_flags=runtime_flags)
    chain = prompt | _llm
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "query_builder")
    response = await chain.ainvoke(
        {
            "schema_plan": state.get("schema_plan"),
            "user_query": state.get("user_query"),
            # "feedback_str": feedback_str,
        }
    )
    content = response.content
    
    # Check for built-in reasoning content in model metadata (additional_kwargs)
    explanation = response.additional_kwargs.get("reasoning_content") or response.additional_kwargs.get("reasonig_content") or ""
    
    # Extract SQL from the response content
    sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    if sql_match:
        sql = sql_match.group(1).strip()
        if not explanation:
            explanation = content.replace(sql_match.group(0), "").strip()
    else:
        # Check for general code block
        block_match = re.search(r"```\s*(.*?)\s*```", content, re.DOTALL)
        if block_match:
            sql = block_match.group(1).strip()
            if not explanation:
                explanation = content.replace(block_match.group(0), "").strip()
        else:
            sql = content.strip()

    if sql.endswith(";"):
        sql = sql[:-1].strip()

    if state.get("non_interactive"):
        return {
            "sql_query": sql,
            "refinement_count": 0,
            "trino_error": None,
            "feedback": None,
            "execution_path": ["query_builder"],
        }

    approval_result = interrupt(
        {
            "type": "query_approval",
            "schema_plan": state.get("schema_plan"),
            "sql_query": sql,
            "sql_explanation": explanation,
        }
    )

    if approval_result.get("approved"):
        return {
            "sql_query": sql,
            "refinement_count": 0,
            "trino_error": None,
            "feedback": None,
            "execution_path": ["query_builder"],
        }
    else:
        return {
            "feedback": approval_result.get("feedback", "Query rejected by user"),
            "sql_query": None,
            "execution_path": ["query_builder"],
        }
