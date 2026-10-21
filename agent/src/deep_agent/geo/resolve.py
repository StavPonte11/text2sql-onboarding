"""
Stage 2 — resolve a normalised location to a provider feature, fetching geometry only as needed.

Geocoders, in order of preference:
- the Jarvis gateway tool `get_geocode_address`, when the gateway lists it (arguments are built
  from its live args_schema; `polygon_geojson` is sent only if the schema has it);
- the in-repo Nominatim client (agent.services.geo_utils.nominatim_search).
A failing gateway call falls back to the in-repo client once; nothing is retried in a loop.

Candidates are filtered by the entity type, ambiguity between distant, similarly ranked
candidates is reported instead of guessed, and the geometry is kept with its real type: a
point is never presented as a polygon, and a bounding box never as a boundary.

Resolved features and their geometries go to the per-thread registry (graph state
`geo_registry`); the model only sees `feature_ref` and compact metadata. SQL refers to a
geometry through a placeholder token ('@<name>_wkt@' — the same token format as the legacy
graph's agent.utils.sql.resolve_wkt_polygons), which execute_trino_query substitutes.
"""

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from shapely.geometry import Point

from agent.config import settings
from agent.services import geo_utils
from deep_agent.geo.geometry import POLYGONAL, GeometryError, parse_geojson, to_wkt
from deep_agent.geo.models import GeometryNeed, ResolvedFeature

logger = logging.getLogger(__name__)

GATEWAY_GEOCODER = "get_geocode_address"
_QUERY_FIELDS = ("q", "query", "address", "location", "search", "text", "location_name", "name")

# Nominatim class / addresstype values that fit each entity type (None = anything goes).
_TYPE_FIT: dict[str, set[str] | None] = {
    "country": {"boundary", "place:country", "country"},
    "region": {"boundary", "place:region", "place:state", "place:county", "place:district", "place:province",
               "region", "state", "county", "district", "place:island", "natural"},
    "district": {"boundary", "place:district", "place:county", "district", "county", "state_district"},
    "city": {"place:city", "place:town", "boundary", "city", "town", "municipality"},
    "town": {"place:town", "place:city", "place:village", "boundary", "town", "city", "village", "municipality"},
    "village": {"place:village", "place:hamlet", "place:town", "boundary", "village", "hamlet", "town"},
    "settlement": {"place", "boundary", "village", "town", "hamlet", "city"},
    "kibbutz": {"place", "boundary", "village", "hamlet"},
    "moshav": {"place", "boundary", "village", "hamlet"},
    "outpost": {"place", "hamlet", "isolated_dwelling"},
    "neighborhood": {"place:suburb", "place:neighbourhood", "place:quarter", "boundary", "suburb", "neighbourhood",
                     "quarter"},
    "refugee_camp": {"place", "landuse", "boundary", "amenity"},
    "military_base": {"landuse:military", "military", "place"},
    "camp": {"landuse", "military", "place", "tourism"},
    "river": {"waterway", "natural:water"},
    "stream": {"waterway", "natural:water"},
    "wadi": {"waterway", "natural:valley", "natural"},
    "lake": {"natural:water", "water"},
    "sea": {"place:sea", "natural:water", "natural:bay"},
    "coastline": {"natural:coastline", "natural:beach", "natural"},
    "mountain": {"natural:peak", "natural:ridge", "natural:hill", "natural:mountain_range", "natural", "place"},
    "hill": {"natural:peak", "natural:hill", "natural:ridge", "natural"},
    "valley": {"natural:valley", "natural", "place", "landuse"},
    "landmark": None,
    "other": None,
}


class GeocoderError(RuntimeError):
    pass


class Geocoder(Protocol):
    name: str

    async def search(self, query: str, polygon: bool, limit: int) -> list[dict]: ...


class NominatimGeocoder:
    name = "nominatim"

    async def search(self, query: str, polygon: bool, limit: int) -> list[dict]:
        try:
            return await asyncio.to_thread(geo_utils.nominatim_search, query, polygon, limit)
        except Exception as exc:
            raise GeocoderError(f"Nominatim request failed: {exc}") from exc


class GatewayGeocoder:
    """Adapter for the gateway's get_geocode_address tool (wrapped in the Deep Agent envelope)."""

    def __init__(self, tool: Any):
        self.tool = tool
        self.name = f"gateway:{GATEWAY_GEOCODER}"
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            props, required = schema.get("properties", {}), schema.get("required", [])
        elif schema is not None:
            js = schema.model_json_schema()
            props, required = js.get("properties", {}), js.get("required", [])
        else:
            props, required = {}, []
        self.props = props
        self.query_field = next((f for f in _QUERY_FIELDS if f in props), None) or next(
            (f for f in required if props.get(f, {}).get("type") == "string"), None)

    def build_args(self, query: str, polygon: bool, limit: int) -> dict[str, Any]:
        if not self.query_field:
            raise GeocoderError(f"{GATEWAY_GEOCODER} schema has no recognisable query field: {sorted(self.props)}")
        args: dict[str, Any] = {self.query_field: query}
        if polygon and "polygon_geojson" in self.props:
            kind = self.props["polygon_geojson"].get("type")
            args["polygon_geojson"] = {"integer": 1, "boolean": True}.get(kind, "1")
        if "limit" in self.props:
            args["limit"] = limit if self.props["limit"].get("type") == "integer" else str(limit)
        return args

    async def search(self, query: str, polygon: bool, limit: int) -> list[dict]:
        args = self.build_args(query, polygon, limit)
        raw = await self.tool.ainvoke(args)
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise GeocoderError(f"{GATEWAY_GEOCODER} returned non-JSON output") from exc
        if isinstance(envelope, dict) and envelope.get("ok") is False:
            raise GeocoderError((envelope.get("error") or {}).get("message") or "gateway geocoder failed")
        data = envelope.get("data") if isinstance(envelope, dict) and "ok" in envelope else envelope
        return provider_items(data)


def provider_items(data: Any) -> list[dict]:
    """Candidate list from a Nominatim-shaped payload (jsonv2 list, GeoJSON FeatureCollection, wrappers)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise GeocoderError("geocoder returned non-JSON text") from exc
    if isinstance(data, dict):
        if data.get("error"):
            raise GeocoderError(f"geocoder error: {data['error']}")
        for key in ("features", "results", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        if data.get("type") == "Feature" or "lat" in data:
            return [data]
        raise GeocoderError(f"unrecognised geocoder payload keys: {sorted(data)[:8]}")
    if isinstance(data, list):
        return data
    raise GeocoderError(f"unrecognised geocoder payload type: {type(data).__name__}")


@dataclass
class Candidate:
    provider_id: str | None
    display_name: str | None
    english_name: str | None
    feature_class: str | None
    addresstype: str | None
    importance: float
    center: tuple[float, float] | None
    bbox: tuple[float, float, float, float] | None
    geojson: dict | None
    notes: list[str] = field(default_factory=list)


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_candidate(item: dict) -> Candidate:
    """One provider item -> Candidate. Handles jsonv2 (lat/lon strings, boundingbox lat-first) and GeoJSON."""
    if item.get("type") == "Feature":
        props = item.get("properties") or {}
        geometry = item.get("geometry")
        center = None
        if geometry and geometry.get("type") == "Point":
            lon, lat = geometry.get("coordinates", [None, None])[:2]
            center = (_float(lon), _float(lat)) if _float(lon) is not None and _float(lat) is not None else None
        bbox = item.get("bbox")
        bbox_t = tuple(map(float, bbox)) if bbox and len(bbox) == 4 else None  # GeoJSON: lon/lat order already
        category = props.get("category") or props.get("class")
    else:
        props = item
        geometry = item.get("geojson")
        lat, lon = _float(item.get("lat")), _float(item.get("lon"))
        center = (lon, lat) if lat is not None and lon is not None else None
        bb = item.get("boundingbox")
        # jsonv2 boundingbox is [min_lat, max_lat, min_lon, max_lon] -> (min_lon, min_lat, max_lon, max_lat)
        bbox_t = (float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])) if bb and len(bb) == 4 else None
        category = item.get("category") or item.get("class")
    osm_type, osm_id = props.get("osm_type"), props.get("osm_id")
    provider_id = f"{osm_type}/{osm_id}" if osm_type and osm_id else (str(props["place_id"]) if props.get("place_id") else None)
    names = props.get("namedetails") or {}
    english = names.get("name:en") if isinstance(names, dict) else None
    ftype = props.get("type")
    return Candidate(
        provider_id=provider_id, display_name=props.get("display_name") or props.get("name"), english_name=english,
        feature_class=f"{category}:{ftype}" if category and ftype else category or ftype,
        addresstype=props.get("addresstype"), importance=_float(props.get("importance")) or 0.0,
        center=center, bbox=bbox_t, geojson=geometry if isinstance(geometry, dict) else None,
    )


def fits_type(candidate: Candidate, entity_type: str | None) -> bool:
    allowed = _TYPE_FIT.get(entity_type or "other")
    if allowed is None:
        return True
    keys = {candidate.feature_class or "", (candidate.feature_class or "").split(":")[0], candidate.addresstype or ""}
    return bool(keys & allowed)


def _distance_km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
    if a is None or b is None:
        return 0.0
    from deep_agent.geo.geometry import geodesic_distance_m
    return geodesic_distance_m(a, b) / 1000


def choose(candidates: list[Candidate], entity_type: str | None) -> tuple[Candidate | None, list[Candidate], str | None]:
    """(chosen, alternatives, problem). problem is set when no candidate can be chosen safely."""
    if not candidates:
        return None, [], "no match"
    fitting = [c for c in candidates if fits_type(c, entity_type)]
    if not fitting:
        return None, candidates, f"no candidate matches the expected type '{entity_type}'"
    fitting.sort(key=lambda c: c.importance, reverse=True)
    top = fitting[0]
    rivals = [
        c for c in fitting[1:]
        if c.importance >= top.importance * settings.GEO_AMBIGUITY_IMPORTANCE_RATIO
        and _distance_km(top.center, c.center) > settings.GEO_AMBIGUITY_DISTANCE_KM
    ]
    if rivals:
        return None, [top, *rivals], "several distant candidates rank similarly"
    return top, [], None


def candidate_summary(c: Candidate) -> dict[str, Any]:
    return {"provider_id": c.provider_id, "display_name": c.display_name, "feature_class": c.feature_class,
            "center_lon_lat": c.center}


_TOKEN_SAFE = re.compile(r"[^a-z0-9_]")


def placeholder(name: str, *parts: str) -> str:
    """Geometry placeholder name (without @…@): '<name>_<parts>_wkt', lowercase [a-z0-9_]."""
    base = "_".join(p for p in (name, *parts) if p)
    token = re.sub(r"_+", "_", _TOKEN_SAFE.sub("_", base.lower())).strip("_") or "loc"
    if token[0].isdigit():
        token = f"loc_{token}"
    return f"{token}_wkt"


def feature_ref(provider: str, candidate: Candidate, query: str) -> str:
    ident = candidate.provider_id or hashlib.sha1(f"{query}|{candidate.center}".encode()).hexdigest()[:10]
    return f"{provider.split(':')[0]}:{ident}"


@dataclass
class Resolution:
    feature: ResolvedFeature | None
    geometry: dict[str, Any] | None = None       # registry entry for the feature's geometry
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    error: tuple[str, str] | None = None          # (category, message)
    limitation: str | None = None


def _geometry_for(candidate: Candidate, need: GeometryNeed) -> tuple[Any, list[str], str | None]:
    """Shapely geometry to register for `need`, notes, and a limitation message if `need` is unmet."""
    if need == "point":
        if candidate.center is None:
            return None, [], "provider returned no representative point"
        return Point(candidate.center), ["provider representative point (not a centroid)"], None
    if candidate.geojson is None:
        if candidate.center is not None:
            return Point(candidate.center), [], f"provider returned no {need} geometry; kept its point"
        return None, [], "provider returned no geometry"
    geom, notes = parse_geojson(candidate.geojson)
    gtype = geom.geom_type
    if need == "area" and gtype not in POLYGONAL:
        return geom, notes, f"provider has no boundary for this feature (returned {gtype})"
    if need == "line" and gtype in {"Point", "MultiPoint"}:
        return geom, notes, f"provider has no line geometry for this feature (returned {gtype})"
    return geom, notes, None


async def resolve(
    query: str, need: GeometryNeed, entity_type: str | None, english_name: str | None,
    geocoders: list[Geocoder], provider_id: str | None = None,
) -> Resolution:
    polygon = need in ("area", "line", "any")
    notes: list[str] = []
    items, used = None, None
    errors = []
    for geocoder in geocoders:
        try:
            items = await geocoder.search(query, polygon, settings.GEO_CANDIDATE_LIMIT)
            used = geocoder
            break
        except GeocoderError as exc:
            logger.warning("Geocoder %s failed for %r: %s", geocoder.name, query, exc)
            errors.append(f"{geocoder.name}: {exc}")
    if used is None:
        return Resolution(None, error=("GEOCODER_UNAVAILABLE", "; ".join(errors) or "no geocoder configured"))
    if errors:
        notes.append(f"fell back to {used.name} after: {'; '.join(errors)}")

    candidates = [normalize_candidate(i) for i in items if isinstance(i, dict)]
    if provider_id:
        picked = [c for c in candidates if c.provider_id == provider_id]
        if not picked:
            return Resolution(None, alternatives=[candidate_summary(c) for c in candidates],
                              error=("LOCATION_NOT_FOUND", f"provider_id {provider_id} is not among the candidates"))
        chosen, alternatives, problem = picked[0], [], None
        notes.append("candidate chosen explicitly by provider_id")
    else:
        chosen, alternatives, problem = choose(candidates, entity_type)
    if chosen is None:
        category = "LOCATION_AMBIGUOUS" if problem and problem.startswith("several") else "LOCATION_NOT_FOUND"
        return Resolution(None, alternatives=[candidate_summary(c) for c in alternatives],
                          error=(category, f"'{query}': {problem}"))

    try:
        geom, geom_notes, limitation = _geometry_for(chosen, need)
    except GeometryError as exc:
        return Resolution(None, error=("MALFORMED_GEOMETRY", f"'{query}': {exc}"))
    notes += geom_notes
    if limitation:
        notes.append(limitation)

    ref = feature_ref(used.name, chosen, query)
    wkt = tolerance = None
    geometry_entry = None
    if geom is not None:
        try:
            wkt, tolerance = to_wkt(geom, settings.GEO_MAX_WKT_CHARS, settings.GEO_MAX_SIMPLIFY_TOLERANCE_M)
        except GeometryError as exc:
            return Resolution(None, error=("GEOMETRY_TOO_COMPLEX", f"'{query}': {exc}"))
        if tolerance:
            notes.append(f"simplified with tolerance {tolerance:.6f}° (≈{tolerance * 111_195:.0f} m), topology preserved")
        token = placeholder(english_name or chosen.english_name or "loc",
                            ref.split(":", 1)[1].replace("/", "_"), geom.geom_type.lower())
        geometry_entry = {"token": token, "wkt": wkt, "geometry_type": geom.geom_type, "feature_ref": ref,
                          "derivation": "provider representative point" if need == "point" or chosen.geojson is None
                          else "provider geometry"}
    feature = ResolvedFeature(
        feature_ref=ref, query=query, provider=used.name, provider_id=chosen.provider_id,
        display_name=chosen.display_name, english_name=english_name or chosen.english_name, entity_type=entity_type,
        feature_class=chosen.feature_class, geometry_type=geom.geom_type if geom is not None else None,
        center=chosen.center, bbox=chosen.bbox, simplify_tolerance_deg=tolerance, notes=notes,
        geometries={geometry_entry["geometry_type"]: geometry_entry["token"]} if geometry_entry else {},
    )
    return Resolution(feature, geometry_entry, limitation=limitation)
