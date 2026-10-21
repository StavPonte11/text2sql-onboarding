"""Provider payloads and fake geocoders for the location pipeline tests (no network)."""

import math

from shapely.geometry import LineString, Point, Polygon, mapping

from deep_agent.geo.geometry import EARTH_RADIUS_M
from deep_agent.geo.resolve import GeocoderError


def offset(lon: float, lat: float, east_m: float = 0.0, north_m: float = 0.0) -> tuple[float, float]:
    """Point east_m / north_m metres away (small offsets, spherical Earth)."""
    dlat = math.degrees(north_m / EARTH_RADIUS_M)
    dlon = math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lon + dlon, lat + dlat


def square(lon: float, lat: float, half_m: float) -> Polygon:
    corners = [offset(lon, lat, e, n) for e, n in ((-half_m, -half_m), (half_m, -half_m), (half_m, half_m),
                                                     (-half_m, half_m))]
    return Polygon(corners)


def item(osm_id, name, cls, typ, lon, lat, geometry=None, importance=0.6, addresstype=None, name_en=None,
         osm_type="relation"):
    """A Nominatim jsonv2 result (lat/lon strings, boundingbox lat-first)."""
    geom = geometry if geometry is not None else Point(lon, lat)
    minx, miny, maxx, maxy = geom.bounds
    result = {
        "place_id": osm_id * 10, "osm_type": osm_type, "osm_id": osm_id, "lat": str(lat), "lon": str(lon),
        "category": cls, "type": typ, "addresstype": addresstype or typ, "importance": importance,
        "display_name": name, "boundingbox": [str(miny), str(maxy), str(minx), str(maxx)],
        "namedetails": {"name": name, **({"name:en": name_en} if name_en else {})},
    }
    if geometry is not None:
        result["geojson"] = mapping(geometry)
    return result


# Beer Sheva-like city: center + ~4 km square boundary.
CITY_CENTER = (34.7913, 31.2520)
CITY_AREA = square(*CITY_CENTER, 4000)
CITY = item(1382494, "באר שבע", "boundary", "administrative", *CITY_CENTER, geometry=CITY_AREA,
            addresstype="city", name_en="Beer Sheva", importance=0.7)
CITY_POINT_ONLY = item(1382494, "באר שבע", "place", "city", *CITY_CENTER, importance=0.7)
RIVER_LINE = LineString([offset(*CITY_CENTER, -6000, 9000), offset(*CITY_CENTER, 6000, 9000)])
RIVER = item(555, "נחל קישון", "waterway", "river", *offset(*CITY_CENTER, 0, 9000), geometry=RIVER_LINE)
RESTAURANT = item(77, "חיפה", "amenity", "restaurant", 34.78, 32.07, osm_type="node", importance=0.65)
HAIFA = item(1382495, "חיפה", "boundary", "administrative", 34.99, 32.79, geometry=square(34.99, 32.79, 5000),
             addresstype="city", importance=0.6)


class FakeGeocoder:
    """Records calls; returns scripted results per (query, polygon)."""

    def __init__(self, results=None, name="nominatim", error=None):
        self.name = name
        self.results = results or {}
        self.error = error
        self.calls: list[tuple[str, bool, int]] = []

    async def search(self, query, polygon, limit):
        self.calls.append((query, polygon, limit))
        if self.error:
            raise GeocoderError(self.error)
        value = self.results.get((query, polygon), self.results.get(query, []))
        return value(polygon) if callable(value) else value
