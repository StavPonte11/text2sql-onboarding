"""
Geometry handling for the location pipeline: validation, simplification, metric buffers.

All coordinates are WGS84 lon/lat (X = longitude, Y = latitude), as in GeoJSON, WKT and
Trino's ST_Point(lon, lat).

Metric buffers ("within 2 km of …") are built here rather than in SQL because Trino's
ST_Distance on SphericalGeography only supports points and ST_Distance / ST_Buffer on
geometry work in degrees. The reference geometry is projected to a local azimuthal
equidistant plane around its centroid (spherical Earth, R = 6 371 008.8 m), buffered in
metres, and projected back. For features up to ~100 km across the scale error is below
0.2 %, well under the precision of "2 km". The buffer is then a polygon that Trino tests with
planar ST_Contains / ST_Intersects.
"""

import math

import shapely
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

EARTH_RADIUS_M = 6_371_008.8
METRES_PER_DEGREE = math.pi * EARTH_RADIUS_M / 180

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString"}
PUNCTUAL = {"Point", "MultiPoint"}


class GeometryError(ValueError):
    """Geometry that cannot be used as returned (malformed, out of range, too complex)."""


def parse_geojson(geojson: dict) -> tuple[BaseGeometry, list[str]]:
    """Validated shapely geometry from a GeoJSON geometry dict, plus notes on any repair."""
    try:
        geom = shape(geojson)
    except Exception as exc:  # shapely raises several types for malformed input
        raise GeometryError(f"malformed GeoJSON geometry: {exc}") from exc
    if geom.is_empty:
        raise GeometryError("empty geometry")
    min_x, min_y, max_x, max_y = geom.bounds
    if not (-180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90):
        raise GeometryError(f"coordinates out of lon/lat range (bounds {geom.bounds})")
    notes = []
    if not geom.is_valid:
        reason = shapely.is_valid_reason(geom)
        repaired = shapely.make_valid(geom)
        if geom.geom_type in POLYGONAL:
            repaired = _polygonal_part(repaired)
        if repaired is None or repaired.is_empty or not repaired.is_valid:
            raise GeometryError(f"invalid geometry that could not be repaired ({reason})")
        notes.append(f"repaired invalid {geom.geom_type} ({reason}) with make_valid")
        geom = repaired
    return geom, notes


def _polygonal_part(geom: BaseGeometry) -> BaseGeometry | None:
    if geom.geom_type in POLYGONAL:
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in POLYGONAL]
    return shapely.union_all(parts) if parts else None


def to_wkt(geom: BaseGeometry, max_chars: int, max_tolerance_m: float) -> tuple[str, float | None]:
    """WKT at 6-decimal precision (~0.1 m); simplified only when longer than max_chars.

    Returns (wkt, tolerance_deg or None). Simplification is topology-preserving with the
    smallest tolerance (binary search) that fits, and never above max_tolerance_m — a larger
    deviation would change which rows match, so GeometryError is raised instead. There is no
    bounding-box fallback: a bbox is not a boundary.
    """
    wkt = shapely.to_wkt(geom, rounding_precision=6)
    if len(wkt) <= max_chars:
        return wkt, None
    low, high = 0.0, max_tolerance_m / METRES_PER_DEGREE
    best: tuple[str, float] | None = None
    candidate = shapely.to_wkt(geom.simplify(high, preserve_topology=True), rounding_precision=6)
    if len(candidate) > max_chars:
        raise GeometryError(
            f"geometry needs more than {max_tolerance_m:g} m of simplification to fit {max_chars} characters")
    best = (candidate, high)
    for _ in range(20):
        mid = (low + high) / 2
        candidate = shapely.to_wkt(geom.simplify(mid, preserve_topology=True), rounding_precision=6)
        if len(candidate) <= max_chars:
            best, high = (candidate, mid), mid
        else:
            low = mid
    return best


def _aeqd(lon0: float, lat0: float):
    """Forward/inverse local azimuthal-equidistant projection (spherical) around (lon0, lat0)."""
    phi0, lam0 = math.radians(lat0), math.radians(lon0)
    sin0, cos0 = math.sin(phi0), math.cos(phi0)

    def forward(lon, lat, z=None):
        phi, lam = math.radians(lat), math.radians(lon)
        cos_c = sin0 * math.sin(phi) + cos0 * math.cos(phi) * math.cos(lam - lam0)
        c = math.acos(max(-1.0, min(1.0, cos_c)))
        k = 1.0 if c == 0 else c / math.sin(c)
        x = EARTH_RADIUS_M * k * math.cos(phi) * math.sin(lam - lam0)
        y = EARTH_RADIUS_M * k * (cos0 * math.sin(phi) - sin0 * math.cos(phi) * math.cos(lam - lam0))
        return x, y

    def inverse(x, y, z=None):
        rho = math.hypot(x, y)
        if rho == 0:
            return lon0, lat0
        c = rho / EARTH_RADIUS_M
        phi = math.asin(math.cos(c) * sin0 + y * math.sin(c) * cos0 / rho)
        lam = lam0 + math.atan2(x * math.sin(c), rho * cos0 * math.cos(c) - y * sin0 * math.sin(c))
        return math.degrees(lam), math.degrees(phi)

    # shapely.ops.transform calls these per coordinate (math.* rejects arrays with TypeError).
    return forward, inverse


def metric_buffer(geom: BaseGeometry, distance_m: float) -> BaseGeometry:
    """Polygon of all points within distance_m metres of `geom` (lon/lat in, lon/lat out)."""
    if distance_m <= 0:
        raise GeometryError("buffer distance must be positive")
    centroid = geom.centroid
    forward, inverse = _aeqd(centroid.x, centroid.y)
    projected = transform(forward, geom)
    buffered = projected.buffer(distance_m, quad_segs=16)
    # Drop vertices that move the outline by < 1 % of the distance (keeps WKT compact).
    buffered = buffered.simplify(distance_m * 0.01, preserve_topology=True)
    return transform(inverse, buffered)


def geodesic_distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lon, lat) points (same sphere as Trino)."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def as_geojson(geom: BaseGeometry) -> dict:
    return mapping(geom)


def point(lon: float, lat: float) -> Point:
    return Point(lon, lat)
