import re
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event_sync
from agent.state import AgentState
from agent.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from agent.config import settings
from agent.langfuse_client import langfuse_client
from langgraph.types import interrupt

from agent.utils.sql import clean_sql


def _build_feedback_and_enrichments_str(
    feedback: str | None,
    loaded_skills: list[str] | None,
    enrichments: list[dict] | None,
    has_location_instruction: bool,
) -> str:
    """Format feedback, applied skills, and non-duplicate query enrichments cleanly."""
    parts = []

    if feedback:
        parts.append(f"User Feedback to apply: {feedback}")

    if loaded_skills:
        from agent.utils.skill_registry import SkillRegistry

        _skill_registry = SkillRegistry()
        skill_prompts = _skill_registry.build_system_prompt_addition(loaded_skills)
        if skill_prompts:
            parts.append(f"[APPLIED SKILLS]{skill_prompts}")

    if enrichments:
        # Filter out location polygon entries if location_wkt_instruction is already provided
        filtered_entries = []
        for e in enrichments:
            if not isinstance(e, dict):
                continue
            ctx = e.get("context", "")
            term = e.get("term", "")
            if has_location_instruction and (
                ctx.startswith("Location '") or "polygon:" in ctx.lower()
            ):
                continue
            filtered_entries.append((term, ctx))

        if filtered_entries:
            enrichment_lines = []
            for term, ctx in filtered_entries:
                if term == "current_time":
                    enrichment_lines.append(f"• Current Time: {ctx}")
                else:
                    enrichment_lines.append(f"• {term}: {ctx}")
            parts.append("[QUERY ENRICHMENTS]\n" + "\n".join(enrichment_lines))

    return "\n\n".join(parts)


async def query_builder_node(state: AgentState, config: RunnableConfig | None = None):
    """Build SQL from catalog and user query."""
    runtime_flags = state.get("runtime_flags") or {}
    location_wkt_instruction = state.get("location_wkt_instruction") or ""

    feedback_str = _build_feedback_and_enrichments_str(
        feedback=state.get("feedback"),
        loaded_skills=state.get("loaded_skills"),
        enrichments=state.get("query_enrichments"),
        has_location_instruction=bool(location_wkt_instruction.strip()),
    )

    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_QUERY_BUILDER)
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
    _llm = get_llm("query_builder", runtime_flags=runtime_flags)
    chain = prompt | _llm
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "query_builder")
    response = await chain.ainvoke(
        {
            "jeen_catalog": state.get("jeen_catalog") or "",
            "user_query": state.get("user_query") or "",
            "feedback_str": feedback_str,
            "location_wkt_instruction": location_wkt_instruction,
        }
    )
    content = response.content

    # Check for built-in reasoning content in model metadata (additional_kwargs)
    explanation = (
        response.additional_kwargs.get("reasoning_content")
        or response.additional_kwargs.get("reasonig_content")
        or ""
    )

    sql = clean_sql(content)

    return {
        "sql_query": sql,
        "sql_explanation": explanation,
        "execution_path": ["query_builder"],
        "refinement_count": 0,
        "trino_error": None,
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
