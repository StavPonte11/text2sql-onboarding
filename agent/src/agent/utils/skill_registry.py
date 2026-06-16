import json
import logging
from typing import Any
from agent.utils.jeen_client import JeenSkillClient
from redis.asyncio import Redis
from agent.config import settings

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry that caches and provides active skills fetched from Jeen."""

    def __init__(self, redis_client: Redis | None = None):
        self.jeen_client = JeenSkillClient()
        self.redis = redis_client
        self.cache_ttl = 300  # 5 minutes

    async def get_skills(self, skill_ids: list[str]) -> list[dict[str, Any]]:
        """
        Fetch skills by ID, trying Redis cache first, then Jeen API.
        """
        if not skill_ids:
            return []

        if not self.jeen_client.is_configured:
            logger.debug("Jeen client not configured. Skipping skill fetch.")
            return []

        loaded_skills = []
        missing_ids = []

        # 1. Try fetching from Redis
        if self.redis and not settings.SKILLS_HOT_RELOAD:
            try:
                keys = [f"skill:{sid}" for sid in skill_ids]
                cached_values = await self.redis.mget(keys)
                for sid, val in zip(skill_ids, cached_values):
                    if val:
                        loaded_skills.append(json.loads(val))
                    else:
                        missing_ids.append(sid)
            except Exception as e:
                logger.error(f"Redis error while fetching skills: {e}")
                missing_ids = skill_ids  # Fallback to fetching all from Jeen
        else:
            missing_ids = skill_ids

        # 2. Fetch missing skills from Jeen
        if missing_ids:
            fetched = await self.jeen_client.fetch_skills_by_ids(missing_ids)
            loaded_skills.extend(fetched)

            # 3. Cache the newly fetched skills
            if self.redis and fetched:
                try:
                    pipeline = self.redis.pipeline()
                    for skill in fetched:
                        key = f"skill:{skill['id']}"
                        pipeline.setex(key, self.cache_ttl, json.dumps(skill))
                    await pipeline.execute()
                except Exception as e:
                    logger.error(f"Redis error while caching skills: {e}")

        return loaded_skills

    def build_system_prompt_addition(self, loaded_skills: list[dict[str, Any]]) -> str:
        """
        Compiles the system prompt fragments of the loaded skills.
        """
        if not loaded_skills:
            return ""

        fragments = []
        for skill in loaded_skills:
            name = skill.get("displayName") or skill.get("name", "Unknown Skill")
            fragment = skill.get("systemPromptFragment")
            if fragment:
                fragments.append(f"### Skill: {name}\n{fragment}")

        if not fragments:
            return ""

        return "\n\n" + "\n\n".join(fragments) + "\n\n"
