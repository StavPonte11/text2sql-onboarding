"""
FlagService (G4-01)
===================
Redis-backed feature flag and execution mode service.

Resolution contract (highest → lowest priority):
  1. config.execution_modes.flag_overrides  (by execution_mode name)
  2. config.feature_flags.value             (DS-managed, cached 30 s)
  3. AgentSettings env-var defaults          (always-on fallback)

Cache keys:
  flag:all            – full dict of all DB flag values         TTL=30s
  mode:{name}         – single mode's flag_overrides dict       TTL=30s

A missing row in config.feature_flags means "no DB override" —
callers must fall back to their env-var default.
"""

import json
import logging
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from core.db.engine import engine
from core.models.models import (
    ExecutionMode,
    ExecutionModeUpsert,
    FeatureFlag,
    FeatureFlagAuditLog,
)
from fastapi import HTTPException
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

FLAG_CACHE_TTL = 30     # seconds — per TTS-G4-01 AC1
MODE_CACHE_TTL = 30     # seconds

# Valid types and coercion rules
_VALID_TYPES = {"bool", "int", "float", "string", "json"}

_REDIS: aioredis.Redis | None = None


def _get_redis(redis_url: str) -> aioredis.Redis:
    global _REDIS
    if _REDIS is None:
        _REDIS = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _REDIS


def validate_flag_type(value: Any, flag_type: str) -> bool:
    """Return True if *value* is compatible with the declared *flag_type*."""
    if flag_type == "bool":
        return isinstance(value, bool)
    if flag_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if flag_type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if flag_type == "string":
        return isinstance(value, str)
    if flag_type == "json":
        return isinstance(value, (dict, list))
    return False


class FlagService:
    """
    Service layer for feature flags and execution modes.
    All methods are synchronous (called from FastAPI sync routes).
    Redis calls are wrapped in try/except — a Redis outage never crashes the API.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    @property
    def _redis(self) -> aioredis.Redis:
        return _get_redis(self._redis_url)

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _try_cache_get(self, key: str) -> dict | None:
        """Synchronous Redis GET (creates a new event loop if needed for sync context)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        try:
            raw = loop.run_until_complete(self._redis.get(key))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Flag cache GET error for %r: %s", key, exc)
        return None

    def _try_cache_set(self, key: str, value: dict, ttl: int) -> None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._redis.setex(key, ttl, json.dumps(value)))
        except Exception as exc:
            logger.warning("Flag cache SET error for %r: %s", key, exc)

    def _invalidate(self, *keys: str) -> None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._redis.delete(*keys))
        except Exception as exc:
            logger.warning("Flag cache DELETE error for %r: %s", keys, exc)

    # ── Audit log ─────────────────────────────────────────────────────────────

    def _write_audit(
        self,
        session: Session,
        flag_name: str,
        actor: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        audit = FeatureFlagAuditLog(
            flag_name=flag_name,
            actor=actor,
            old_value=old_value,
            new_value=new_value,
            changed_at=datetime.utcnow(),
        )
        session.add(audit)
        # caller is responsible for commit

    # ── Feature Flag CRUD ─────────────────────────────────────────────────────

    def list_all(self) -> list[FeatureFlag]:
        """Return all flags from DB (no cache — always fresh for UI display)."""
        with Session(engine) as session:
            return session.exec(select(FeatureFlag)).all()

    def get_map(self) -> dict[str, Any]:
        """
        Return {name: value} dict for all flags, using cache when warm.
        Used by FlagBridge inside the agent.
        """
        cached = self._try_cache_get("flag:all")
        if cached is not None:
            return cached

        with Session(engine) as session:
            flags = session.exec(select(FeatureFlag)).all()

        flag_map = {f.name: f.value for f in flags}
        self._try_cache_set("flag:all", flag_map, FLAG_CACHE_TTL)
        return flag_map

    def set(self, name: str, value: Any, actor: str) -> FeatureFlag:
        """
        Upsert a flag value. Validates type, writes audit log, invalidates cache.
        Raises HTTPException(422) on type mismatch, HTTPException(404) if flag unknown.
        """
        with Session(engine) as session:
            flag = session.get(FeatureFlag, name)
            if flag is None:
                raise HTTPException(status_code=404, detail=f"Flag '{name}' not found")

            if not validate_flag_type(value, flag.type):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Type mismatch: flag '{name}' expects type '{flag.type}', "
                        f"but received value of Python type '{type(value).__name__}'"
                    ),
                )

            old_value = flag.value
            flag.value = value
            flag.last_modified_by = actor
            flag.last_modified_at = datetime.utcnow()
            session.add(flag)
            self._write_audit(session, name, actor, old_value, value)
            session.commit()
            session.refresh(flag)

        self._invalidate("flag:all")
        return flag

    def delete(self, name: str, actor: str) -> None:
        """
        Reset a flag to its env-var default by deleting the DB row.
        Writes audit log with new_value=None to mark the reset.
        """
        with Session(engine) as session:
            flag = session.get(FeatureFlag, name)
            if flag is None:
                raise HTTPException(status_code=404, detail=f"Flag '{name}' not found")

            old_value = flag.value
            self._write_audit(session, name, actor, old_value, None)
            # Reset: clear value rather than deleting the row so metadata is preserved
            flag.value = None
            flag.last_modified_by = actor
            flag.last_modified_at = datetime.utcnow()
            session.add(flag)
            session.commit()

        self._invalidate("flag:all")

    # ── Execution Mode CRUD ───────────────────────────────────────────────────

    def list_modes(self) -> list[ExecutionMode]:
        with Session(engine) as session:
            return session.exec(select(ExecutionMode)).all()

    def get_mode(self, name: str) -> ExecutionMode | None:
        with Session(engine) as session:
            return session.get(ExecutionMode, name)

    def get_mode_overrides(self, name: str) -> dict[str, Any]:
        """Return the flag_overrides dict for a named mode, with caching."""
        cache_key = f"mode:{name}"
        cached = self._try_cache_get(cache_key)
        if cached is not None:
            return cached

        with Session(engine) as session:
            mode = session.get(ExecutionMode, name)

        if mode is None:
            return {}
        overrides = mode.flag_overrides or {}
        self._try_cache_set(cache_key, overrides, MODE_CACHE_TTL)
        return overrides

    def upsert_mode(self, name: str, data: ExecutionModeUpsert, actor: str) -> ExecutionMode:
        with Session(engine) as session:
            mode = session.get(ExecutionMode, name)
            if mode is None:
                mode = ExecutionMode(name=name, created_by=actor)
            mode.description = data.description
            mode.flag_overrides = data.flag_overrides
            mode.is_active = data.is_active
            mode.updated_at = datetime.utcnow()
            session.add(mode)
            session.commit()
            session.refresh(mode)

        self._invalidate(f"mode:{name}")
        return mode

    def delete_mode(self, name: str) -> None:
        with Session(engine) as session:
            mode = session.get(ExecutionMode, name)
            if mode is None:
                raise HTTPException(status_code=404, detail=f"Execution mode '{name}' not found")
            session.delete(mode)
            session.commit()

        self._invalidate(f"mode:{name}")
