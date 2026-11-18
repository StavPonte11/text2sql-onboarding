"""
resolve_location_to_wkt: a place name (Hebrew or English) to its area polygon as WKT, via Nominatim.
"""

from langchain_core.tools import tool

from agent.services.geo_utils import geojson_to_simplified_wkt, get_geojson_polygon
from deep_agent.tools.envelope import LOCATION_NOT_FOUND, error, fail, ok


@tool
def resolve_location_to_wkt(location_name: str) -> str:
    """
    Resolves a geographic location name to its WKT (Well-Known Text) polygon representation.

    Use this whenever the user's query involves a geographic area and you need a WKT polygon for
    a Trino ST_GeometryFromText() / ST_Within() / ST_Contains() call.

    Supports Hebrew location names directly (e.g., 'תל אביב', 'ירושלים', 'חיפה').
    Returns data.wkt — the polygon as plain (unquoted) WKT, e.g. POLYGON((...)). In SQL, wrap it
    in single quotes: ST_GeometryFromText('<data.wkt>').

    The function uses rate-limited Nominatim geocoding with caching and automatic WKT
    simplification (binary search). Only area geometries are returned (never points).

    Do NOT use this for exact addresses (e.g., "Rothschild 10, Tel Aviv"); it is designed for
    administrative areas: cities, regions, districts, and countries.

    Args:
        location_name (str): The geographic location name to resolve. Hebrew names are
                             accepted and preferred for Israeli locations.
    """
    geojson = get_geojson_polygon(location_name)
    quoted = geojson_to_simplified_wkt(geojson) if geojson else None  # returns a single-quoted literal
    if not quoted:
        return fail(error(LOCATION_NOT_FOUND, f"Could not resolve location '{location_name}' to an area polygon."))
    return ok({"location": location_name, "wkt": quoted.strip("'")})
