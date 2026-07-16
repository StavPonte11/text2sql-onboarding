"""
geo_utils.py - Geocoding and geometry utility functions for standalone node.
"""

import requests
import json
import logging
import time
from functools import lru_cache
from typing import Optional
from shapely.geometry import shape
 
logger = logging.getLogger(__name__)

@lru_cache(maxsize=128)
def get_geojson_polygon(location_name: str) -> Optional[dict]:
    """
    Fetches GeoJSON polygon for a Hebrew location name.
    Returns the 'geometry' dict if found, else None.
    """
    # Enforce Nominatim policy: max 1 request per second
    time.sleep(1)
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": location_name,
            "accept-language": "he",
            "polygon_geojson": "1",
            "limit": "1",
            "format": "geojson"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        res = requests.get(url, params=params, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()

        features = data.get("features", [])
        if not features:
            return None

        geom = features[0].get("geometry")
        if not geom:
            return None

        return geom
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error(f"Geocoding API request failed for '{location_name}': {e}")
        return None


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
    for _ in range(25):
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
        import shapely.wkt
        envelope_geom = geom_shape.envelope
        envelope_wkt = f"'{shapely.wkt.dumps(envelope_geom, rounding_precision=4)}'"
        if len(envelope_wkt) <= max_length:
            logger.warning("Simplification exceeded character limit. Fell back to bounding box (envelope).")
            return envelope_wkt
    except Exception as e:
        logger.error(f"Failed to calculate envelope fallback: {e}")

    return None
