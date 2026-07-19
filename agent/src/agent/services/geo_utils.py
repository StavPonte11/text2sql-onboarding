"""
geo_utils.py - Geocoding and geometry utility functions for standalone node.
"""

import json
import logging
import threading
import time
from functools import lru_cache
from typing import Optional
import shapely.wkt

import requests
from shapely.geometry import shape

from agent.config import settings

logger = logging.getLogger(__name__)

# ── Geometry type allow-list ──────────────────────────────────────────────────

_AREA_TYPES = {"Polygon", "MultiPolygon"}

# ── Thread-safe Nominatim rate limiter ────────────────────────────────────────
# Enforces ≥1 s between outbound requests across all threads (Nominatim ToS).

_rate_lock = threading.Lock()
_last_request_time: float = 0.0


def _nominatim_wait() -> None:
    """Block the calling thread until at least 1 s has elapsed since the last
    Nominatim request.  The lock guarantees only one thread enters the critical
    section at a time, so concurrent callers queue rather than fire together."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        gap = settings.NOMINATIM_RATE_LIMIT_SECONDS - (now - _last_request_time)
        if gap > 0:
            time.sleep(gap)
        _last_request_time = time.monotonic()


# ── Geocoding ─────────────────────────────────────────────────────────────────


@lru_cache(maxsize=128)
def _fetch_geojson_polygon(location_name: str) -> Optional[dict]:
    """Inner cached fetch.

    Returns:
        geometry dict  — for a valid Polygon / MultiPolygon result.
        None           — when Nominatim legitimately has no area for the name
                         (stable result, worth caching).

    Raises:
        requests.exceptions.RequestException | json.JSONDecodeError
                       — on transient network / parse failures.
                         lru_cache does NOT cache exceptions, so a later call
                         may retry successfully.
    """
    _nominatim_wait()

    url = settings.NOMINATIM_URL
    params = {
        "q": location_name,
        "accept-language": "he",
        "polygon_geojson": "1",
        "limit": "1",
        "format": "geojson",
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

    res = requests.get(url, params=params, headers=headers, timeout=settings.NOMINATIM_TIMEOUT)
    res.raise_for_status()          # raises RequestException on 4xx/5xx
    data = res.json()               # raises JSONDecodeError on bad body

    features = data.get("features", [])
    if not features:
        return None

    geom = features[0].get("geometry")
    if not geom:
        return None

    # Finding 4 – reject non-area geometry types before returning
    geom_type = geom.get("type")
    if geom_type not in _AREA_TYPES:
        logger.warning(
            "Nominatim returned non-area geometry type '%s' for '%s'; skipping.",
            geom_type,
            location_name,
        )
        return None

    return geom


def get_geojson_polygon(location_name: str) -> Optional[dict]:
    """Public wrapper around _fetch_geojson_polygon.

    Transient request / JSON failures return None here without being stored by
    the cache, so a subsequent call can retry. Stable 'not found' None results
    from the inner function are cached normally.

    Exposes cache_clear() and cache_info() from the inner lru_cache so callers
    (including test fixtures) can manage the cache via the public API.
    """
    try:
        return _fetch_geojson_polygon(location_name)
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error("Geocoding API request failed for '%s': %s", location_name, e)
        return None


# Expose lru_cache management methods on the public function so callers that
# relied on the old @lru_cache surface (e.g. test fixtures calling
# get_geojson_polygon.cache_clear()) continue to work unchanged.
get_geojson_polygon.cache_clear = _fetch_geojson_polygon.cache_clear  # type: ignore[attr-defined]
get_geojson_polygon.cache_info  = _fetch_geojson_polygon.cache_info   # type: ignore[attr-defined]


def geojson_to_simplified_wkt(geojson_geom: dict, max_length: int = 2100) -> Optional[str]:
    """
    Converts GeoJSON geometry to a simplified, quoted WKT string.
    Uses binary search to ensure WKT length <= max_length.
    If even tolerance=1.0 exceeds limit, falls back to the bounding box (envelope).
    """
    try:
        geom_shape = shape(geojson_geom)
    except Exception as e:
        logger.error(f"Failed to parse GeoJSON to Shapely shape: {e}")
        return None

    # Step 1: Try initial fine simplification
    try:
        simplified = geom_shape.simplify(0.0001, preserve_topology=True)
    except Exception as e:
        logger.error(f"Shapely simplify failed: {e}")
        return None

    wkt_str = f"'{simplified.wkt}'"
    if len(wkt_str) <= max_length:
        return wkt_str

    # Step 2: Binary Search Optimization
    # Tolerance range: 0.0001 to 1.0
    low = 0.0001
    high = 1.0
    best_wkt = None

    # Binary search optimal tolerance
    for _ in range(settings.NOMINATIM_SIMPLIFY_ITERATIONS):
        mid = (low + high) / 2
        try:
            simpl_mid = geom_shape.simplify(mid, preserve_topology=True)
            wkt_mid = f"'{simpl_mid.wkt}'"
            if len(wkt_mid) <= max_length:
                best_wkt = wkt_mid
                high = mid  # Try smaller tolerance (finer detail)
            else:
                low = mid  # Too long, make it coarser
        except Exception:
            low = mid

    if best_wkt:
        return best_wkt

    # Step 3: Bounding box fallback if even tolerance=1.0 exceeded limit
    try:
        envelope_geom = geom_shape.envelope
        envelope_wkt = f"'{shapely.wkt.dumps(envelope_geom, rounding_precision=4)}'"
        if len(envelope_wkt) <= max_length:
            logger.warning("Simplification exceeded character limit. Fell back to bounding box (envelope).")
            return envelope_wkt
    except Exception as e:
        logger.error(f"Failed to calculate envelope fallback: {e}")

    return None
