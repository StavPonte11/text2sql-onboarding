from agent.state import AgentState
from agent.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from agent.langfuse_client import langfuse_client
from langgraph.types import interrupt
def query_builder_node(state: AgentState):
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
    response = chain.invoke(
        {
            "schema_plan": state.get("schema_plan"),
            "user_query": state["user_query"],
            "feedback_str": feedback_str,
        }
    )
    sql = response.content.replace("```sql", "").replace("```", "").strip()
    if sql.endswith(";"):
        sql = sql[:-1].strip()

    if state.get("non_interactive"):
        return {
            "sql_query": sql,
            "refinement_count": 0,
            "trino_error": None,
            "feedback": None,
        }

    approval_result = interrupt(
        {
            "type": "query_approval",
            "schema_plan": state.get("schema_plan"),
            "sql_query": sql,
        }
    )

    if approval_result.get("approved"):
        return {
            "sql_query": sql,
            "refinement_count": 0,
            "trino_error": None,
            "feedback": None,
        }
    else:
        return {
            "feedback": approval_result.get("feedback", "Query rejected by user"),
            "sql_query": None,
        }
