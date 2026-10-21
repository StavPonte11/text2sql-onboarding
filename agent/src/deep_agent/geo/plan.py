"""
Stage 3 — check the model's spatial usage plan against evidence and render its SQL deterministically.

The location-analyst model decides, per location, the operation and representation after
inspecting the schema and data. finalize_plan() then:
- checks the plan against what tools actually returned: categorical values and column names
  must appear in a tool result, feature_refs must exist in the registry, and the geometry type
  must fit the operation (no containment in a point, no boundary distance from a center);
- requires a distance for proximity (user-given, or a configured per-entity-type default) —
  never a silent universal radius;
- renders geometry_sql / predicate_sql from registry geometries, building metre-accurate
  buffers (deep_agent.geo.geometry.metric_buffer) and registering them as new placeholders;
- sets status and issues on every usage.

Predicates are fragments for the parent agent (columns unqualified); the parent owns the query.
"""

import re
from typing import Any

import shapely

from agent.config import settings
from deep_agent.geo.geometry import LINEAR, POLYGONAL, PUNCTUAL, GeometryError, metric_buffer, to_wkt
from deep_agent.geo.models import LocationPlan, LocationUsage
from deep_agent.geo.resolve import placeholder

_SIMPLE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_SPATIAL_OPS = {"within_area", "intersects_area", "within_distance", "directional"}


def quote_ident(name: str) -> str:
    name = name.strip()
    if name.startswith('"') and name.endswith('"'):
        return name
    return name if _SIMPLE_IDENT.match(name) else '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _bare(name: str) -> str:
    return name.strip().strip('"').lower()


def geometry_literal(token: str) -> str:
    return f"ST_GeometryFromText('@{token}@')"


class _Finalizer:
    def __init__(self, registry: dict[str, Any], observed: str):
        self.features: dict[str, dict] = registry.get("features") or {}
        self.geometries: dict[str, dict] = registry.get("geometries") or {}
        self.observed = observed.lower()
        self.observed_raw = observed
        self.new_geometries: dict[str, dict] = {}

    # -- evidence ---------------------------------------------------------------------------------
    def _column_seen(self, name: str | None) -> bool:
        """The column name occurs as a whole identifier in a schema/data tool result ("lat" ≠ "relation")."""
        return bool(name) and re.search(rf"(?<![\w]){re.escape(_bare(name))}(?![\w])", self.observed) is not None

    def _check_target(self, usage: LocationUsage, kinds: set[str]) -> list[str]:
        t = usage.target
        if t is None:
            return ["no target columns given"]
        if t.kind not in kinds:
            return [f"target kind '{t.kind}' cannot express {usage.operation}"]
        cols = [t.lon_column, t.lat_column] if t.kind == "lon_lat" else [t.column]
        if any(not c for c in cols):
            return [f"target kind '{t.kind}' needs " + ("lon_column and lat_column" if t.kind == "lon_lat" else "column")]
        unseen = [c for c in cols if not self._column_seen(c)]
        return [f"column {c} was not seen in any schema/data tool result" for c in unseen]

    def _target_sql(self, usage: LocationUsage) -> str:
        t = usage.target
        if t.kind == "lon_lat":
            return f"ST_Point({quote_ident(t.lon_column)}, {quote_ident(t.lat_column)})"
        if t.kind == "wkt":
            return f"ST_GeometryFromText({quote_ident(t.column)})"
        return quote_ident(t.column)

    # -- geometry -----------------------------------------------------------------------------------
    def _feature(self, usage: LocationUsage) -> dict | None:
        return self.features.get(usage.feature_ref or "")

    def _geometry(self, feature: dict, types: set[str]) -> dict | None:
        for gtype, token in (feature.get("geometries") or {}).items():
            if gtype in types and token in self.geometries:
                return self.geometries[token]
        return None

    def _register(self, base_token: str, suffix: str, geom, feature_ref: str, derivation: str) -> dict:
        token = placeholder(base_token.removesuffix("_wkt"), suffix)
        wkt, tolerance = to_wkt(geom, settings.GEO_MAX_WKT_CHARS, settings.GEO_MAX_SIMPLIFY_TOLERANCE_M)
        entry = {"token": token, "wkt": wkt, "geometry_type": geom.geom_type, "feature_ref": feature_ref,
                 "derivation": derivation + (f"; simplified {tolerance:.6f}°" if tolerance else "")}
        self.new_geometries[token] = entry
        return entry

    def _center(self, feature: dict) -> tuple[float, float] | None:
        center = feature.get("center")
        if center:
            return tuple(center)
        point = self._geometry(feature, {"Point"})
        if point:
            p = shapely.from_wkt(point["wkt"])
            return p.x, p.y
        return None

    # -- operations ---------------------------------------------------------------------------------
    def category(self, usage: LocationUsage) -> None:
        issues = self._check_target(usage, {"categorical"})
        if not usage.category_values:
            issues.append("no stored category values given")
        unseen = [v for v in usage.category_values if v not in self.observed_raw]
        issues += [f"value {v!r} was not seen in any tool result — verify it with the column's value tools"
                   for v in unseen]
        usage.representation = "categorical"
        if issues:
            usage.status, usage.issues = "unresolved", issues
            return
        col = quote_ident(usage.target.column)
        values = usage.category_values
        usage.predicate_sql = (f"{col} = {quote_literal(values[0])}" if len(values) == 1
                               else f"{col} IN ({', '.join(quote_literal(v) for v in values)})")
        usage.status = "ready"

    def area(self, usage: LocationUsage, feature: dict) -> None:
        issues = self._check_target(usage, {"lon_lat", "wkt", "geometry"})
        geom = self._geometry(feature, POLYGONAL)
        if geom is None:
            types = sorted((feature.get("geometries") or {}).keys()) or ["none"]
            issues.append(f"{usage.operation} needs an area geometry; the resolved feature has {', '.join(types)} "
                          "(resolve with geometry='area', or use a distance from its point instead)")
            usage.status, usage.issues = "unsupported", issues
            return
        if issues:
            usage.status, usage.issues = "unresolved", issues
            return
        usage.representation, usage.geometry_type = "area", geom["geometry_type"]
        usage.geometry_sql = geometry_literal(geom["token"])
        fn = "ST_Contains" if usage.operation == "within_area" else "ST_Intersects"
        usage.predicate_sql = f"{fn}({usage.geometry_sql}, {self._target_sql(usage)})"
        usage.status = "ready"

    def _distance(self, usage: LocationUsage, feature: dict) -> float | None:
        if usage.distance_m and usage.distance_m > 0:
            if usage.distance_source is None:
                usage.distance_source = "user"
            return usage.distance_m
        entity_type = feature.get("entity_type")
        default = settings.GEO_PROXIMITY_DEFAULTS_M.get(entity_type or "")
        if default:
            usage.distance_m, usage.distance_source = float(default), "configured_default"
            usage.assumptions.append(f"configured default proximity for {entity_type}: {default:g} m")
            return float(default)
        return None

    def distance(self, usage: LocationUsage, feature: dict) -> None:
        issues = self._check_target(usage, {"lon_lat", "wkt", "geometry"})
        d = self._distance(usage, feature)
        if d is None:
            usage.status = "ambiguous"
            usage.ambiguity = usage.ambiguity or (
                "proximity without a distance — ask the user for one (no default is configured for this place type)")
            usage.issues = issues
            return
        ref = usage.distance_reference or "center"
        usage.distance_reference = ref
        if ref in ("center", "point"):
            center = self._center(feature)
            if center is None:
                usage.status = "unsupported"
                usage.issues = [*issues, "no provider point for this feature — resolve with geometry='point'"]
                return
            if issues:
                usage.status, usage.issues = "unresolved", issues
                return
            usage.representation, usage.geometry_type = "point", "Point"
            lon, lat = center
            t = usage.target
            if t.kind == "lon_lat":
                usage.geometry_sql = f"ST_Point({lon:.6f}, {lat:.6f})"
                usage.predicate_sql = (f"great_circle_distance({quote_ident(t.lat_column)}, {quote_ident(t.lon_column)}, "
                                       f"{lat:.6f}, {lon:.6f}) <= {d / 1000:g}")
            else:
                buf = self._register(f"{feature.get('english_name') or 'loc'}_center", f"buf{d:g}m",
                                     metric_buffer(shapely.Point(lon, lat), d), usage.feature_ref,
                                     f"{d:g} m metric buffer around the provider point")
                usage.geometry_sql = geometry_literal(buf["token"])
                usage.predicate_sql = f"ST_Intersects({usage.geometry_sql}, {self._target_sql(usage)})"
            usage.status = "ready"
            return

        wanted = {"area": POLYGONAL, "boundary_line": POLYGONAL, "line": LINEAR | POLYGONAL}[ref]
        geom = self._geometry(feature, wanted)
        if geom is None:
            types = sorted((feature.get("geometries") or {}).keys()) or ["none"]
            need = "an area" if ref in ("area", "boundary_line") else "a line"
            usage.status = "unsupported"
            usage.issues = [*issues, f"distance from the {ref.replace('_', ' ')} needs {need} geometry; the resolved "
                                     f"feature has {', '.join(types)} — do not substitute its center point"]
            return
        if issues:
            usage.status, usage.issues = "unresolved", issues
            return
        shape = shapely.from_wkt(geom["wkt"])
        if ref == "boundary_line":
            shape = shape.boundary
        elif ref == "line" and geom["geometry_type"] in POLYGONAL:
            usage.assumptions.append("the provider maps this feature as an area (e.g. a riverbank); distance is "
                                     "measured from that area")
        try:
            buf = self._register(geom["token"], f"{ref}_buf{d:g}m", metric_buffer(shape, d), usage.feature_ref,
                                 f"{d:g} m metric buffer around the {ref.replace('_', ' ')}")
        except GeometryError as exc:
            usage.status, usage.issues = "unsupported", [str(exc)]
            return
        usage.representation = "line" if ref in ("line", "boundary_line") else "area"
        usage.geometry_type = geom["geometry_type"]
        usage.geometry_sql = geometry_literal(buf["token"])
        target = self._target_sql(usage)
        fn = "ST_Contains" if usage.target.kind == "lon_lat" else "ST_Intersects"
        usage.predicate_sql = f"{fn}({usage.geometry_sql}, {target})"
        usage.status = "ready"

    def directional(self, usage: LocationUsage, feature: dict) -> None:
        bbox = feature.get("bbox")
        usage.status = "needs_parent_judgment"
        usage.issues.append(
            "directional relations are not rendered automatically — decide the reference (center vs. extent) and "
            f"state it as an assumption; provider extent (min_lon, min_lat, max_lon, max_lat) = {bbox}, "
            f"center (lon, lat) = {feature.get('center')}")

    def run(self, usage: LocationUsage) -> None:
        usage.status, usage.issues = None, []
        usage.geometry_sql = usage.predicate_sql = usage.geometry_type = None
        if usage.ambiguity:
            usage.status = "ambiguous"
            return
        if usage.operation == "none":
            usage.status = "ready"
            return
        if usage.operation == "category_match":
            self.category(usage)
            return
        feature = self._feature(usage)
        if feature is None:
            usage.status = "unresolved"
            usage.issues = [f"feature_ref {usage.feature_ref!r} was not resolved in this thread — call resolve_location"]
            return
        if usage.operation in ("within_area", "intersects_area"):
            self.area(usage, feature)
        elif usage.operation == "within_distance":
            self.distance(usage, feature)
        else:
            self.directional(usage, feature)


def finalize_plan(plan: LocationPlan, registry: dict[str, Any], observed: str) -> tuple[LocationPlan, dict[str, dict]]:
    """Validated, rendered copy of `plan` and the derived geometries to add to the registry."""
    plan = plan.model_copy(deep=True)
    finalizer = _Finalizer(registry, observed)
    for usage in plan.usages:
        finalizer.run(usage)

    by_mention: dict[str, set[str]] = {}
    for usage in plan.usages:
        if usage.operation != "none":
            by_mention.setdefault(usage.canonical_hebrew, set()).add(
                "categorical" if usage.operation == "category_match" else "spatial")
    note = (plan.combination_note or "").lower()
    for usage in plan.usages:
        if by_mention.get(usage.canonical_hebrew) == {"categorical", "spatial"} and " or " not in f" {note} ":
            usage.issues.append("this location has both a categorical and a spatial predicate — they are combined "
                                "with AND unless combination_note justifies OR by the data model")
    if " or " in f" {note} " and "hybrid" not in note and "some rows" not in note:
        plan.combination_note = (plan.combination_note or "") + \
            " [warning: OR between location predicates needs a data-model justification (e.g. some rows only have " \
            "coordinates); otherwise use AND or pick one representation]"
    return plan, finalizer.new_geometries
