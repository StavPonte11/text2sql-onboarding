"""
Stage 3: representation decisions are checked against evidence and rendered SQL is semantically right.

Semantics are verified on controlled points at known distances: the rendered buffer polygons are
evaluated with shapely (the same planar point-in-polygon test Trino's ST_Contains does), and the
center-radius predicate with the same great-circle formula Trino's great_circle_distance uses.
"""

import re

import pytest
import shapely

from agent.config import settings
from deep_agent.geo.geometry import geodesic_distance_m, metric_buffer
from deep_agent.geo.models import LocationPlan, LocationUsage, TargetColumns
from deep_agent.geo.plan import finalize_plan
from tests.deep_agent.geo.fixtures import CITY_CENTER, CITY_AREA, RIVER, RIVER_LINE, FakeGeocoder, item, offset
from tests.deep_agent.geo.test_resolution import Thread, city_results
from deep_agent.geo.tools import make_resolve_location_tool

EVENTS = '"minio"."public"."events"'
OBSERVED = (
    '{"ok":true,"data":[{"name":"city","type":"varchar","semanticType":"categorical"},'
    '{"name":"lon","type":"double"},{"name":"lat","type":"double"},{"name":"geom_wkt","type":"varchar"}]}\n'
    '{"ok":true,"data":{"rows":[["באר שבע"],["חיפה"]]}}'
)
LON_LAT = TargetColumns(table=EVENTS, kind="lon_lat", lon_column="lon", lat_column="lat")


async def registry_with(*specs):
    """Resolve (query, geometry, entity_type, english, results) specs into a thread registry; return refs."""
    thread, refs = Thread(), []
    for query, geometry, entity_type, english, results in specs:
        tool = make_resolve_location_tool([FakeGeocoder({query: results})])
        out = await thread.call(tool, canonical_hebrew=query, geometry=geometry, entity_type=entity_type,
                                english_name=english)
        refs.append(out["data"]["feature_ref"])
    return thread.state["geo_registry"], refs


def usage(**kw) -> LocationUsage:
    base = {"original_mention": "באר שבע", "canonical_hebrew": "באר שבע", "english_name": "beer_sheva", "intent": "x"}
    return LocationUsage(**{**base, **kw})


def run(registry, *usages, note=None):
    plan, new = finalize_plan(LocationPlan(usages=list(usages), combination_note=note), registry, OBSERVED)
    return plan, {**(registry.get("geometries") or {}), **new}


def geometry_of(u: LocationUsage, geometries) -> shapely.Geometry:
    token = re.search(r"@([a-z0-9_]+_wkt)@", u.predicate_sql).group(1)
    return shapely.from_wkt(geometries[token]["wkt"])


# 1. Categorical column answers it -> no geometry -------------------------------------------------------

def test_1_categorical_filter_without_geocoding():
    plan, _ = run({}, usage(intent="incidents in Beer Sheva", operation="category_match",
                            target=TargetColumns(table=EVENTS, kind="categorical", column="city"),
                            category_values=["באר שבע"]))
    u = plan.usages[0]
    assert u.status == "ready" and u.representation == "categorical"
    assert u.predicate_sql == "city = 'באר שבע'" and u.geometry_sql is None and u.feature_ref is None


def test_1b_categorical_value_quotes_are_escaped():
    plan, _ = finalize_plan(LocationPlan(usages=[usage(operation="category_match", category_values=["O'Hare"],
                                                       target=TargetColumns(table=EVENTS, kind="categorical", column="city"))]),
                            {}, OBSERVED + " O'Hare")
    assert plan.usages[0].predicate_sql == "city = 'O''Hare'"


# 2. Radius around the city center -> provider point + great-circle distance ----------------------------

@pytest.mark.asyncio
async def test_2_radius_around_center():
    registry, [ref] = await registry_with(("באר שבע", "point", "city", "beer_sheva", city_results))
    plan, _ = run(registry, usage(intent="within 2 km of the center", operation="within_distance", target=LON_LAT,
                                  feature_ref=ref, distance_m=2000, distance_reference="center"))
    u = plan.usages[0]
    assert u.status == "ready" and u.representation == "point" and u.distance_source == "user"
    lon, lat = CITY_CENTER
    assert u.predicate_sql == f"great_circle_distance(lat, lon, {lat:.6f}, {lon:.6f}) <= 2"
    # lat-first argument order + km: points at 1.9 / 2.1 km
    for metres, inside in ((1900, True), (2100, False)):
        p = offset(lon, lat, east_m=metres)
        assert (geodesic_distance_m(CITY_CENTER, p) / 1000 <= 2) is inside


# 3. Distance to the municipal boundary -> boundary geometry, not the centroid --------------------------

@pytest.mark.asyncio
async def test_3_distance_to_boundary_uses_the_boundary():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    plan, geometries = run(registry, usage(intent="within 2 km of the municipal boundary", operation="within_distance",
                                           target=LON_LAT, feature_ref=ref, distance_m=2000,
                                           distance_reference="boundary_line"))
    u = plan.usages[0]
    assert u.status == "ready" and u.representation == "line" and u.predicate_sql.startswith("ST_Contains(")
    band = geometry_of(u, geometries)
    lon, lat = CITY_CENTER  # the city square has a 4 km half-width
    cases = {
        offset(lon, lat, east_m=4000 + 1500): True,   # 1.5 km outside the boundary
        offset(lon, lat, east_m=4000 - 1500): True,   # 1.5 km inside the boundary
        offset(lon, lat, east_m=4000 + 2500): False,  # 2.5 km outside
        CITY_CENTER: False,                           # 4 km from the boundary although "in the city"
    }
    for point, expected in cases.items():
        assert band.contains(shapely.Point(point)) is expected, point


@pytest.mark.asyncio
async def test_3b_within_city_or_up_to_2km_outside_uses_the_area():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    plan, geometries = run(registry, usage(operation="within_distance", target=LON_LAT, feature_ref=ref,
                                           distance_m=2000, distance_reference="area"))
    area = geometry_of(plan.usages[0], geometries)
    assert area.contains(shapely.Point(CITY_CENTER))
    assert area.contains(shapely.Point(offset(*CITY_CENTER, east_m=5900)))
    assert not area.contains(shapely.Point(offset(*CITY_CENTER, east_m=6100)))


@pytest.mark.asyncio
async def test_3c_boundary_distance_with_only_a_point_is_refused():
    registry, [ref] = await registry_with(("באר שבע", "point", "city", "beer_sheva", city_results))
    plan, _ = run(registry, usage(operation="within_distance", target=LON_LAT, feature_ref=ref, distance_m=2000,
                                  distance_reference="boundary_line"))
    u = plan.usages[0]
    assert u.status == "unsupported" and u.predicate_sql is None
    assert "do not substitute its center point" in u.issues[-1]


# 4. Proximity to a river -> line geometry + an explicit distance --------------------------------------

@pytest.mark.asyncio
async def test_4_near_river_uses_the_line():
    registry, [ref] = await registry_with(("נחל קישון", "line", "river", "kishon_river", [RIVER]))
    river = usage(original_mention="נחל קישון", canonical_hebrew="נחל קישון", english_name="kishon_river",
                  intent="within 500 m of the Kishon", operation="within_distance", target=LON_LAT, feature_ref=ref,
                  distance_m=500, distance_reference="line")
    plan, geometries = run(registry, river)
    u = plan.usages[0]
    assert u.status == "ready" and u.representation == "line" and u.geometry_type == "LineString"
    corridor = geometry_of(u, geometries)
    on_line = RIVER_LINE.interpolate(0.5, normalized=True)
    assert corridor.contains(shapely.Point(offset(on_line.x, on_line.y, north_m=400)))
    assert not corridor.contains(shapely.Point(offset(on_line.x, on_line.y, north_m=600)))


@pytest.mark.asyncio
async def test_4b_near_without_distance_is_ambiguous_not_a_default():
    registry, [ref] = await registry_with(("נחל קישון", "line", "river", "kishon_river", [RIVER]))
    plan, _ = run(registry, usage(operation="within_distance", target=LON_LAT, feature_ref=ref, distance_reference="line"))
    u = plan.usages[0]
    assert u.status == "ambiguous" and u.distance_m is None and u.predicate_sql is None
    assert "ask the user" in u.ambiguity


@pytest.mark.asyncio
async def test_4c_configured_default_is_applied_and_reported(monkeypatch):
    monkeypatch.setattr(settings, "GEO_PROXIMITY_DEFAULTS_M", {"river": 300.0})
    registry, [ref] = await registry_with(("נחל קישון", "line", "river", "kishon_river", [RIVER]))
    plan, _ = run(registry, usage(operation="within_distance", target=LON_LAT, feature_ref=ref, distance_reference="line"))
    u = plan.usages[0]
    assert u.status == "ready" and u.distance_m == 300 and u.distance_source == "configured_default"
    assert any("configured default" in a for a in u.assumptions)


# 5. Containment -> area geometry --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_5_containment_uses_the_area():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    plan, geometries = run(registry, usage(intent="inside the municipal boundary", operation="within_area",
                                           target=LON_LAT, feature_ref=ref))
    u = plan.usages[0]
    token = registry["features"][ref]["geometries"]["Polygon"]
    assert u.status == "ready" and u.geometry_type == "Polygon"
    assert u.predicate_sql == f"ST_Contains(ST_GeometryFromText('@{token}@'), ST_Point(lon, lat))"
    area = geometry_of(u, geometries)
    assert area.equals_exact(CITY_AREA, tolerance=1e-6)  # the real boundary, not its bounding box or a buffer


@pytest.mark.asyncio
async def test_5b_wkt_target_column():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    plan, _ = run(registry, usage(operation="intersects_area", feature_ref=ref,
                                  target=TargetColumns(table=EVENTS, kind="wkt", column="geom_wkt")))
    assert plan.usages[0].predicate_sql.endswith(", ST_GeometryFromText(geom_wkt))")
    assert plan.usages[0].predicate_sql.startswith("ST_Intersects(")


# 6. A categorical column that does not express the requested operation ---------------------------------

@pytest.mark.asyncio
async def test_6_region_not_stored_in_the_city_column():
    envelope = item(7, "עוטף עזה", "boundary", "administrative", 34.5, 31.4,
                    geometry=shapely.box(34.4, 31.3, 34.6, 31.55))
    registry, [ref] = await registry_with(("עוטף עזה", "area", "region", "gaza_envelope", [envelope]))
    common = {"original_mention": "עוטף עזה", "canonical_hebrew": "עוטף עזה", "english_name": "gaza_envelope"}
    plan, _ = run(registry,
                  usage(**common, operation="category_match", category_values=["עוטף עזה"],
                        target=TargetColumns(table=EVENTS, kind="categorical", column="city")),
                  usage(**common, operation="within_area", target=LON_LAT, feature_ref=ref))
    categorical, spatial = plan.usages
    assert categorical.status == "unresolved" and "was not seen" in categorical.issues[0]
    assert spatial.status == "ready"


# 7. Geometry / spatial capability unavailable ---------------------------------------------------------

@pytest.mark.asyncio
async def test_7_table_without_coordinates_or_feature_without_area():
    registry, [ref] = await registry_with(("באר שבע", "point", "city", "beer_sheva", city_results))
    plan, _ = run(registry,
                  usage(operation="within_area", feature_ref=ref,
                        target=TargetColumns(table=EVENTS, kind="categorical", column="city")),
                  usage(operation="within_area", feature_ref=ref, target=LON_LAT),
                  usage(operation="within_area", feature_ref=ref,
                        target=TargetColumns(table=EVENTS, kind="lon_lat", lon_column="x_coord", lat_column="lat")))
    a, b, c = plan.usages
    assert a.status == "unsupported" and any("cannot express" in i for i in a.issues)
    assert b.status == "unsupported" and "needs an area geometry" in b.issues[0]
    assert all(u.predicate_sql is None for u in plan.usages)
    assert any("x_coord" in i for i in c.issues)  # an invented column is caught


# 8. Ambiguous or unresolved location -----------------------------------------------------------------

def test_8_unresolved_and_ambiguous():
    plan, _ = run({},
                  usage(operation="within_area", target=LON_LAT, feature_ref="nominatim:relation/404"),
                  usage(operation="within_area", target=LON_LAT, ambiguity="Kafr Kanna (Galilee) or Kafr Kanna (Lebanon)"))
    missing, ambiguous = plan.usages
    assert missing.status == "unresolved" and "resolve_location" in missing.issues[0]
    assert ambiguous.status == "ambiguous" and ambiguous.predicate_sql is None


# 9. Categorical + spatial constraints without a blind OR ------------------------------------------

@pytest.mark.asyncio
async def test_9_mixed_constraints_are_separate_and_anded():
    registry, [river_ref] = await registry_with(("נחל קישון", "line", "river", "kishon_river", [RIVER]))
    plan, _ = run(registry,
                  usage(operation="category_match", category_values=["חיפה"],
                        target=TargetColumns(table=EVENTS, kind="categorical", column="city"),
                        original_mention="חיפה", canonical_hebrew="חיפה", english_name="haifa"),
                  usage(operation="within_distance", target=LON_LAT, feature_ref=river_ref, distance_m=500,
                        distance_reference="line", original_mention="נחל קישון", canonical_hebrew="נחל קישון"))
    assert [u.status for u in plan.usages] == ["ready", "ready"]
    assert all(" OR " not in u.predicate_sql.upper() for u in plan.usages)
    assert all(not u.issues for u in plan.usages)


@pytest.mark.asyncio
async def test_9b_same_place_two_representations_and_unjustified_or():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    cat = usage(operation="category_match", category_values=["באר שבע"],
                target=TargetColumns(table=EVENTS, kind="categorical", column="city"))
    geo = usage(operation="within_area", target=LON_LAT, feature_ref=ref)
    plan, _ = run(registry, cat, geo)
    assert all("combined with AND" in u.issues[-1] for u in plan.usages)
    plan, _ = run(registry, cat, geo, note="city = X OR inside the polygon")
    assert "warning" in plan.combination_note
    plan, _ = run(registry, cat, geo, note="hybrid data: some rows only have coordinates, so city OR polygon")
    assert "warning" not in plan.combination_note


# 10 / directional --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_directional_is_left_to_the_parent_with_the_extent():
    registry, [ref] = await registry_with(("באר שבע", "area", "city", "beer_sheva", city_results))
    plan, _ = run(registry, usage(operation="directional", direction="north", target=LON_LAT, feature_ref=ref))
    u = plan.usages[0]
    assert u.status == "needs_parent_judgment" and u.predicate_sql is None and "extent" in u.issues[0]


def test_model_cannot_supply_rendered_fields():
    forged = usage(operation="category_match", category_values=["nope"], predicate_sql="1=1", status="ready",
                   target=TargetColumns(table=EVENTS, kind="categorical", column="city"))
    plan, _ = run({}, forged)
    assert plan.usages[0].status == "unresolved" and plan.usages[0].predicate_sql is None


def test_metric_buffer_is_isotropic_in_metres():
    lon, lat = 35.0, 33.0
    ring = metric_buffer(shapely.Point(lon, lat), 1000)
    for east, north in ((990, 0), (0, 990), (-700, 700)):
        assert ring.contains(shapely.Point(offset(lon, lat, east, north)))
    for east, north in ((1010, 0), (0, 1010), (-720, 720)):
        assert not ring.contains(shapely.Point(offset(lon, lat, east, north)))
