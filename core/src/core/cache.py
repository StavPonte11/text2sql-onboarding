"""
G2-05: Redis Schema Cache
=========================
Shared CacheService that wraps the system Redis engine.
Exposes a standard get / set / invalidate / invalidate_pattern interface.

Key conventions
---------------
DDL content            ddl:{catalog}:{schema}:{table}         SCHEMA_CACHE_TTL (600s)
Table profile stats    profile:{table_id}:{profile_version}   PROFILE_CACHE_TTL (1800s)
Catalog preflight      catalog_valid:{schema}:{tables_hash}   fixed 300s

All cache calls are wrapped in try/except so a Redis outage never
crashes the application — callers receive None on miss or error and
must fall back to the live data source.
"""

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Fixed TTL for catalog pre-flight validation cache (not configurable)
CATALOG_VALID_TTL = 300


class CacheService:
    """Async Redis cache wrapper with non-blocking fallback semantics."""

    def __init__(self, redis_url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=False,  # return raw bytes so callers control decoding
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    # ── Primitive operations ──────────────────────────────────────────────────

    async def get(self, key: str) -> bytes | None:
        """Return cached bytes for *key*, or None on miss / error."""
        try:
            return await self._redis.get(key)
        except Exception as exc:
            logger.warning("Cache GET error for key %r: %s", key, exc)
            return None

    async def set(self, key: str, value: str | bytes, ttl: int) -> None:
        """Store *value* under *key* with the given TTL (seconds)."""
        if isinstance(value, str):
            value = value.encode()
        try:
            await self._redis.setex(key, ttl, value)
        except Exception as exc:
            logger.warning("Cache SET error for key %r: %s", key, exc)

    async def invalidate(self, key: str) -> None:
        """Delete a single cache key."""
        try:
            await self._redis.delete(key)
        except Exception as exc:
            logger.warning("Cache DELETE error for key %r: %s", key, exc)

    async def invalidate_pattern(self, pattern: str) -> None:
        """
        Delete all keys matching *pattern* using SCAN (safe for production Redis,
        does not block with KEYS).
        """
        try:
            cursor = 0
            pipe = self._redis.pipeline()
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    pipe.delete(*keys)
                if cursor == 0:
                    break
            await pipe.execute()
        except Exception as exc:
            logger.warning("Cache SCAN/DELETE error for pattern %r: %s", pattern, exc)

    # ── JSON convenience helpers ──────────────────────────────────────────────

    async def get_json(self, key: str) -> Any | None:
        """Return deserialized JSON value, or None."""
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        """Serialize *value* to JSON and store with TTL."""
        await self.set(key, json.dumps(value, default=str), ttl)

    # ── Named key builders ────────────────────────────────────────────────────

    @staticmethod
    def ddl_key(catalog: str, schema: str, table: str) -> str:
        return f"ddl:{catalog}:{schema}:{table}"

    @staticmethod
    def profile_key(table_id: str, profile_version: str | int) -> str:
        return f"profile:{table_id}:{profile_version}"

    @staticmethod
    def catalog_valid_key(schema: str, tables: list[str]) -> str:
        tables_hash = hashlib.sha1(
            json.dumps(sorted(tables), separators=(",", ":")).encode()
        ).hexdigest()[:12]
        return f"catalog_valid:{schema}:{tables_hash}"

    # ── Profile invalidation helper ───────────────────────────────────────────

    async def invalidate_profile(self, table_id: str) -> None:
        """
        Purge all cached profile versions for a given table.
        Call this after any background profiling worker completes.
        """
        await self.invalidate_pattern(f"profile:{table_id}:*")


# ── Singleton factory ─────────────────────────────────────────────────────────

_cache_instance: CacheService | None = None


def get_cache_service(redis_url: str) -> CacheService:
    """Return a module-level singleton CacheService."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheService(redis_url)
    return _cache_instance
