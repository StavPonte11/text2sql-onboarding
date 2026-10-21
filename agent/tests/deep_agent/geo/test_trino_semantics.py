"""
Semantic check of rendered location predicates on a real Trino (optional).

Runs only when GEO_TRINO_TEST_HOST is set (e.g. `docker run -p 18080:8080 trinodb/trino:443` and
GEO_TRINO_TEST_HOST=localhost:18080). Each case evaluates the plan's predicate_sql, with the geometry
placeholders substituted exactly as execute_trino_query does, over controlled points whose expected
membership is known, so a predicate that runs but means something else fails.
"""

import os

import pytest

from deep_agent.geo.models import LocationPlan, TargetColumns
from deep_agent.geo.plan import finalize_plan
from deep_agent.geo.tools import substitute_geometries
from tests.deep_agent.geo.fixtures import CITY_CENTER, RIVER, RIVER_LINE, offset
from tests.deep_agent.geo.test_plan import OBSERVED, registry_with, usage
from tests.deep_agent.geo.test_resolution import city_results

HOST = os.getenv("GEO_TRINO_TEST_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="GEO_TRINO_TEST_HOST not set (no Trino to run spatial SQL on)")
LON_LAT = TargetColumns(table="t", kind="lon_lat", lon_column="lon", lat_column="lat")


def run_trino(sql: str) -> list[list]:
    import trino

    host, port = HOST.split(":")
    conn = trino.dbapi.connect(host=host, port=int(port), user="geo-test")
    cur = conn.cursor()
    cur.execute(sql)
    return cur.fetchall()


def matches(predicate: str, registry: dict, points: dict[str, tuple[float, float]]) -> set[str]:
    rows = ", ".join(f"('{label}', {lon!r}, {lat!r})" for label, (lon, lat) in points.items())
    sql = f"SELECT label FROM (VALUES {rows}) AS t(label, lon, lat) WHERE {predicate}"
    return {r[0] for r in run_trino(substitute_geometries(sql, registry))}


async def planned(geometry, results, query, entity_type, english, **kw):
    registry, [ref] = await registry_with((query, geometry, entity_type, english, results))
    plan, new = finalize_plan(LocationPlan(usages=[usage(feature_ref=ref, target=LON_LAT, **kw)]), registry, OBSERVED)
    u = plan.usages[0]
    assert u.status == "ready", u.issues
    return u.predicate_sql, {**registry, "geometries": {**registry["geometries"], **new}}


@pytest.mark.asyncio
async def test_center_radius_on_trino():
    predicate, registry = await planned("point", city_results, "באר שבע", "city", "beer_sheva",
                                        operation="within_distance", distance_m=2000, distance_reference="center")
    lon, lat = CITY_CENTER
    points = {"in_1900m": offset(lon, lat, 1900), "out_2100m": offset(lon, lat, 2100),
              "in_north_1900m": offset(lon, lat, 0, 1900), "out_north_2100m": offset(lon, lat, 0, 2100)}
    assert matches(predicate, registry, points) == {"in_1900m", "in_north_1900m"}


@pytest.mark.asyncio
async def test_containment_on_trino():
    predicate, registry = await planned("area", city_results, "באר שבע", "city", "beer_sheva", operation="within_area")
    lon, lat = CITY_CENTER  # 4 km half-width square
    points = {"center": CITY_CENTER, "in_3900m": offset(lon, lat, 3900), "out_4100m": offset(lon, lat, 4100),
              "swapped_lat_lon": (lat, lon)}
    assert matches(predicate, registry, points) == {"center", "in_3900m"}


@pytest.mark.asyncio
async def test_boundary_band_on_trino():
    predicate, registry = await planned("area", city_results, "באר שבע", "city", "beer_sheva",
                                        operation="within_distance", distance_m=2000, distance_reference="boundary_line")
    lon, lat = CITY_CENTER
    points = {"out_1500m": offset(lon, lat, 5500), "in_1500m": offset(lon, lat, 2500),
              "out_2500m": offset(lon, lat, 6500), "center": CITY_CENTER}
    assert matches(predicate, registry, points) == {"out_1500m", "in_1500m"}


@pytest.mark.asyncio
async def test_river_corridor_on_trino():
    predicate, registry = await planned("line", [RIVER], "נחל קישון", "river", "kishon_river",
                                        operation="within_distance", distance_m=500, distance_reference="line")
    mid = RIVER_LINE.interpolate(0.5, normalized=True)
    points = {"400m": offset(mid.x, mid.y, 0, 400), "600m": offset(mid.x, mid.y, 0, -600),
              "on_line": (mid.x, mid.y)}
    assert matches(predicate, registry, points) == {"400m", "on_line"}
