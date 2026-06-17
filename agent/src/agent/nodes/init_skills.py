import logging
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from agent.utils.skill_registry import SkillRegistry
from python_core_utils.redis import get_redis_client

logger = logging.getLogger(__name__)

_skill_registry = SkillRegistry()

async def init_skills_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """
    Initial node to load skills into the agent state.
    This ensures that skill IO and caching happens at the graph boundary,
    keeping reasoning nodes pure and state reproducible.
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    import asyncio
    asyncio.create_task(publish_node_event(thread_id, "init_skills"))

    active_skills = state.get("active_skills")
    runtime_flags = state.get("runtime_flags") or {}
    
    from agent.config import settings
    skills_enabled = bool(runtime_flags.get("SKILLS_ENABLED", getattr(settings, "SKILLS_ENABLED", True)))
    hot_reload = bool(runtime_flags.get("SKILLS_HOT_RELOAD", getattr(settings, "SKILLS_HOT_RELOAD", False)))
    cache_ttl = int(runtime_flags.get("SKILLS_CACHE_TTL", getattr(settings, "SKILLS_CACHE_TTL", 3600)))
    
    if not skills_enabled or not active_skills:
        return {"loaded_skills": None, "execution_path": ["init_skills"]}
        
    try:
        _skill_registry.redis = get_redis_client()
        loaded_skills = await _skill_registry.get_skills(
            active_skills,
            hot_reload=hot_reload,
            cache_ttl=cache_ttl,
        )
        
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

        return {"loaded_skills": loaded_skills, "execution_path": ["init_skills"]}
    except Exception as e:
        logger.warning(f"Failed to initialize skills: {e}")
        return {"loaded_skills": None, "execution_path": ["init_skills"]}
