import logging
from agent.state import AgentState
from agent.utils.skill_registry import SkillRegistry
from python_core_utils.redis import get_redis_client

logger = logging.getLogger(__name__)

_skill_registry = SkillRegistry()

async def init_skills_node(state: AgentState) -> dict:
    """
    Initial node to load skills into the agent state.
    This ensures that skill IO and caching happens at the graph boundary,
    keeping reasoning nodes pure and state reproducible.
    """
    active_skills = state.get("active_skills")
    
    if not active_skills:
        return {"loaded_skills": None}
        
    try:
        _skill_registry.redis = get_redis_client()
        loaded_skills = await _skill_registry.get_skills(active_skills)
        
        if loaded_skills:
            try:
                from agent.langfuse_client import langfuse_client
                trace_id = langfuse_client.get_current_trace_id()
                if trace_id:
                    skill_names = [s.get("displayName") or s.get("name", "Unknown") for s in loaded_skills]
                    langfuse_client.trace(
                        id=trace_id,
                        metadata={"skills_loaded": skill_names}
                    )
            except Exception as inner_e:
                logger.warning(f"Failed to push skills_loaded to langfuse: {inner_e}")

        return {"loaded_skills": loaded_skills}
    except Exception as e:
        logger.warning(f"Failed to initialize skills: {e}")
        return {"loaded_skills": None}
