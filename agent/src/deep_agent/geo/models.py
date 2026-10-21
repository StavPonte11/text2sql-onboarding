"""
Contracts between the three location stages and the parent agent.

    extract_locations  -> LocationEntity        (what was mentioned, normalised)
    resolve_location   -> ResolvedFeature       (which real-world feature, which geometry)
    LocationPlan       -> LocationUsage         (how each location is used in SQL)

Geometry lives only in ResolvedFeature registry entries (graph state `geo_features`);
the model refers to it by `feature_ref`, and the SQL that embeds it is rendered
deterministically (deep_agent.geo.plan), never copied from model text.
"""

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "country", "region", "district", "city", "town", "village", "settlement", "kibbutz", "moshav", "outpost",
    "neighborhood", "refugee_camp", "military_base", "camp", "river", "stream", "wadi", "lake", "sea", "coastline",
    "mountain", "hill", "valley", "landmark", "other",
]

GeometryNeed = Literal["point", "line", "area", "any"]
GeometryType = Literal[
    "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"
]


class LocationEntity(BaseModel):
    original_mention: str = Field(description="Exact substring of the user query.")
    canonical_hebrew: str
    english_name: str | None = Field(
        default=None, description="lowercase snake_case identifier; a name, not a unique feature id")
    entity_type: EntityType | None = None
    mention_context: str = ""
    normalization_notes: list[str] = Field(default_factory=list)
    ambiguity: str | None = None
    source: Literal["gazetteer", "model_hint"] = "gazetteer"


class ExcludedMention(BaseModel):
    mention: str
    reason: str


class ExtractionResult(BaseModel):
    locations: list[LocationEntity] = Field(default_factory=list)
    excluded: list[ExcludedMention] = Field(default_factory=list)


class ResolvedFeature(BaseModel):
    """One provider feature, as the provider returned it (geometry kept out of tool output)."""

    feature_ref: str                       # stable key into the per-thread registry, e.g. "nominatim:R1382494"
    query: str
    provider: str                          # "nominatim" | "gateway:get_geocode_address"
    provider_id: str | None = None         # e.g. "relation/1382494" or place_id
    display_name: str | None = None
    english_name: str | None = None
    entity_type: str | None = None         # from extraction; drives type filtering and proximity defaults
    feature_class: str | None = None       # Nominatim class/type, e.g. "boundary/administrative"
    geometry_type: GeometryType | None = None
    center: tuple[float, float] | None = None   # (lon, lat) — the provider's representative point
    bbox: tuple[float, float, float, float] | None = None  # (min_lon, min_lat, max_lon, max_lat), never a boundary
    geometries: dict[str, str] = Field(default_factory=dict)  # geometry type -> placeholder token in the registry
    simplify_tolerance_deg: float | None = None
    notes: list[str] = Field(default_factory=list)


Operation = Literal["category_match", "within_area", "intersects_area", "within_distance", "directional", "none"]
Representation = Literal["categorical", "point", "line", "area"]
DistanceReference = Literal["center", "area", "boundary_line", "line", "point"]
Status = Literal["ready", "needs_parent_judgment", "ambiguous", "unresolved", "unsupported"]


class TargetColumns(BaseModel):
    """Where the location is compared in the data. Names must come from tool results, verbatim."""

    table: str
    kind: Literal["categorical", "lon_lat", "wkt", "geometry"]
    column: str | None = None              # categorical / wkt / geometry column
    lon_column: str | None = None
    lat_column: str | None = None


class LocationUsage(BaseModel):
    original_mention: str
    canonical_hebrew: str
    english_name: str | None = None
    intent: str = Field(description="The geographic relation the user asked for, in a few words.")
    operation: Operation
    representation: Representation | None = None
    target: TargetColumns | None = None
    category_values: list[str] = Field(
        default_factory=list, description="Exact stored values for category_match, copied from a tool result.")
    feature_ref: str | None = Field(default=None, description="From resolve_location; required for spatial operations.")
    distance_m: float | None = Field(default=None, description="Only when the user gave a distance (or a configured default applies).")
    distance_source: Literal["user", "configured_default"] | None = None
    distance_reference: DistanceReference | None = None
    direction: Literal["north", "south", "east", "west"] | None = None
    evidence: list[str] = Field(default_factory=list, description="Short facts from tool results that justify the choice.")
    assumptions: list[str] = Field(default_factory=list)
    ambiguity: str | None = None
    # Filled by LocationPlanMiddleware — never by the model.
    status: Status | None = None
    geometry_type: GeometryType | None = None
    geometry_sql: str | None = None
    predicate_sql: str | None = None
    issues: list[str] = Field(default_factory=list)


class LocationPlan(BaseModel):
    """Submit the location plan for the parent agent. Call exactly once, when done."""

    usages: list[LocationUsage] = Field(default_factory=list)
    excluded: list[ExcludedMention] = Field(default_factory=list)
    combination_note: str | None = Field(
        default=None,
        description="How the usages combine (AND by default). Explain any OR — it must be justified by the data model.")
