"""
FlagBridge (G4 Agent Integration)
===================================
Resolves runtime flag values for a single agent invocation.

Resolution order (highest → lowest priority):
  1. Execution mode overrides  (config.execution_modes.flag_overrides by name)
  2. DB flag overrides          (config.feature_flags.value — cached 30s)
  3. AgentSettings env defaults (always-on fallback when backend unreachable)

Usage:
    bridge = FlagBridge()
    flags = await bridge.resolve_flags(execution_mode="cost_saving")
    model = flags.get("QUERY_BUILDER_MODEL", settings.LLM_MODEL)
"""

import logging
from typing import Any

import httpx
from agent.config import settings

logger = logging.getLogger(__name__)

# Default env-var flag map — used as fallback when backend is unreachable
_ENV_DEFAULTS: dict[str, Any] = {
    # Extraction
    "EXTRACTOR_MODEL": settings.LLM_MODEL,
    "EXTRACTOR_TEMPERATURE": 0.0,
    "EXTRACTOR_TOP_K_TABLES": settings.HYBRID_SEARCH_MAX_TABLES,
    "DEFAULT_TABLE_SCOPING_MODE": settings.DEFAULT_TABLE_SCOPING_MODE,
    # Schema Explorer
    "MAX_PROFILES_TO_FETCH": settings.MAX_PROFILES_TO_FETCH,
    "PROFILE_FETCH_CONCURRENCY": settings.PROFILE_FETCH_CONCURRENCY,
    "SCHEMA_CACHE_TTL": settings.SCHEMA_CACHE_TTL,
    "PROFILE_CACHE_TTL": settings.PROFILE_CACHE_TTL,
    "SCHEMA_SEMANTIC_TYPING": settings.ENABLE_SEMANTIC_TYPING,
    "SCHEMA_JOIN_GRAPH": settings.ENABLE_JOIN_GRAPH,
    "SCHEMA_SUMMARIZATION": settings.ENABLE_SCHEMA_SUMMARIZATION,
    "SCHEMA_AMBIGUITY_DETECT": settings.ENABLE_AMBIGUITY_DETECT,
    "SCHEMA_EXPLORER_MODEL": settings.LLM_MODEL,
    "SCHEMA_TOP_K_JOINS": 5,
    # Query Builder
    "QUERY_BUILDER_MODEL": settings.LLM_MODEL,
    "QUERY_BUILDER_TEMPERATURE": 0.0,
    # Refiner
    "MAX_REFINER_ITERATIONS": 4,
    "MAX_SCHEMA_REPLAN_ITERATIONS": 2,
    "REFINER_MODEL": settings.LLM_MODEL,
    # Satisfaction Check
    "SATISFACTION_CHECK_ENABLED": settings.SATISFACTION_CHECK_ENABLED,
    "SATISFACTION_CHECK_EXECUTION": settings.SATISFACTION_CHECK_EXECUTION,
    "SATISFACTION_CHECK_PLAUSIBILITY": settings.SATISFACTION_CHECK_PLAUSIBILITY,
    "SATISFACTION_CHECK_COLUMNS": settings.SATISFACTION_CHECK_COLUMNS,
    "SATISFACTION_CHECK_SEMANTIC": settings.SATISFACTION_CHECK_SEMANTIC,
    "SATISFACTION_MIN_ROWS": settings.SATISFACTION_MIN_ROWS,
    "SATISFACTION_MAX_ROWS": settings.SATISFACTION_MAX_ROWS,
    "SATISFACTION_SEMANTIC_THRESHOLD": settings.SATISFACTION_SEMANTIC_THRESHOLD,
    "SATISFACTION_JUDGE_MODEL": settings.LLM_MODEL,
    # Skills
    "SKILLS_ENABLED": True,
    "SKILLS_HOT_RELOAD": settings.SKILLS_HOT_RELOAD,
    "SKILLS_CACHE_TTL": 900,
    # Evaluation
    "LLM_JUDGE_ENABLED": True,
    "EVAL_PARALLEL_WORKERS": 4,
    "EVAL_JUDGE_MODEL": settings.LLM_MODEL,
    # Catalog Validation
    "CATALOG_VALIDATION_ENABLED": True,
    "CATALOG_CACHE_TTL": 300,
}


class FlagBridge:
    """
    Lightweight async HTTP client that fetches flag values from the Studio backend.
    Falls back gracefully if BACKEND_URL is not set or backend is unreachable.
    """

    def __init__(self) -> None:
        self._base_url = settings.BACKEND_URL.rstrip("/") if settings.BACKEND_URL else ""

    async def resolve_flags(self, execution_mode: str | None = None) -> dict[str, Any]:
        """
        Build and return the fully-merged runtime flag map for this invocation.

        Steps:
          1. Start with env-var defaults (always available).
          2. Overlay DB flag values fetched from backend (if reachable).
          3. Overlay execution mode overrides (if a mode name is given).
        """
        # Layer 1: env defaults
        resolved: dict[str, Any] = dict(_ENV_DEFAULTS)

        if not self._base_url:
            logger.debug("FlagBridge: BACKEND_URL not set, using env-var defaults only")
            if execution_mode:
                logger.warning(
                    "FlagBridge: execution_mode='%s' requested but BACKEND_URL is not set",
                    execution_mode,
                )
            return resolved

        # Layer 2: DB flag overrides
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/flags/map")
                if resp.status_code == 200:
                    db_flags: dict[str, Any] = resp.json()
                    # Only overlay flags that have a non-null value in the DB
                    for name, value in db_flags.items():
                        if value is not None:
                            resolved[name] = value
                    logger.debug("FlagBridge: loaded %d DB flag overrides", len(db_flags))
                else:
                    logger.warning(
                        "FlagBridge: /flags/map returned %d, using env defaults",
                        resp.status_code,
                    )
        except Exception as exc:
            logger.warning("FlagBridge: failed to fetch flag map: %s — using env defaults", exc)

        # Layer 3: execution mode overrides
        if execution_mode and execution_mode != "default":
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(
                        f"{self._base_url}/flags/modes/{execution_mode}"
                    )
                    if resp.status_code == 200:
                        mode_data = resp.json()
                        overrides: dict = mode_data.get("flag_overrides") or {}
                        # Only apply overrides that have non-null values (mirrors DB overlay logic)
                        for name, value in overrides.items():
                            if value is not None:
                                resolved[name] = value
                        logger.info(
                            "FlagBridge: applied execution_mode='%s' (%d overrides)",
                            execution_mode,
                            len(overrides),
                        )
                    elif resp.status_code == 404:
                        logger.warning(
                            "FlagBridge: execution_mode='%s' not found in DB",
                            execution_mode,
                        )
                    else:
                        logger.warning(
                            "FlagBridge: /flags/modes/%s returned %d",
                            execution_mode,
                            resp.status_code,
                        )
            except Exception as exc:
                logger.warning(
                    "FlagBridge: failed to fetch mode '%s': %s", execution_mode, exc
                )

        return resolved
