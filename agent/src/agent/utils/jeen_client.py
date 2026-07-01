import httpx
import logging
from typing import Any
from agent.config import settings

logger = logging.getLogger(__name__)


class JeenSkillClient:
    """Client for fetching skills from the Jeen platform's internal API."""

    def __init__(self):
        self.base_url = settings.JEEN_LLM_CORE_URL.rstrip("/")
        self.api_key = settings.JEEN_API_KEY
        self.is_configured = bool(self.base_url and self.api_key)

        if not self.is_configured:
            logger.info(
                "JeenSkillClient is not fully configured (JEEN_LLM_CORE_URL or JEEN_API_KEY missing). "
                "Skill fetching will be skipped."
            )

    async def fetch_skills_by_ids(self, skill_ids: list[str]) -> list[dict[str, Any]]:
        """
        Fetch active skills from Jeen by their UUIDs.
        Uses POST /admin/assets/skills/by-ids.
        """
        if not self.is_configured or not skill_ids:
            return []

        url = f"{self.base_url}/admin/assets/skills/by-ids"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "ids": skill_ids,
            "page": 1,
            "limit": len(skill_ids) + 10,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                # AdminAssetsPaginatedResponseDto returns { items: [...], total, page, ... }
                items = data.get("items", [])
                
                # Filter only active skills
                active_skills = [s for s in items if s.get("isActive")]
                
                logger.info(f"Successfully fetched {len(active_skills)} active skills from Jeen.")
                return active_skills
        except Exception as e:
            logger.error(f"Failed to fetch skills from Jeen API: {e}", exc_info=True)
            # Do not fall, the agent should pass and ignore if an error occurs
            return []
