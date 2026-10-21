import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import shapely
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from agent.config import settings
from agent.services import geo_utils
from deep_agent.context import merge_geo_registry
from deep_agent.geo.geometry import GeometryError, parse_geojson, to_wkt
from deep_agent.geo.resolve import GatewayGeocoder, GeocoderError, NominatimGeocoder, normalize_candidate, provider_items, resolve
from deep_agent.geo.tools import build_geocoders, make_resolve_location_tool, substitute_geometries
from tests.deep_agent.geo.fixtures import CITY, CITY_CENTER, HAIFA, RESTAURANT, RIVER, FakeGeocoder, item


def without_geometry(result):
    return {k: v for k, v in result.items() if k != "geojson"}


def city_results(polygon):
    return [CITY if polygon else without_geometry(CITY)]


# --- In-repo Nominatim client: argument types --------------------------------------------------------

@pytest.fixture
def nominatim(monkeypatch):
    geo_utils.nominatim_search.cache_clear()
    monkeypatch.setattr(geo_utils, "_nominatim_wait", lambda: None)
    response = MagicMock()
    response.json.return_value = [CITY]
    with patch.object(geo_utils.requests, "get", return_value=response) as get:
        yield get
    geo_utils.nominatim_search.cache_clear()


def test_nominatim_sends_polygon_geojson_as_string_only_when_needed(nominatim):
    geo_utils.nominatim_search("באר שבע", polygon=True, limit=5)
    params = nominatim.call_args.kwargs["params"]
    assert params["polygon_geojson"] == "1" and params["format"] == "jsonv2" and params["limit"] == "5"
    geo_utils.nominatim_search("באר שבע", polygon=False, limit=5)
    assert "polygon_geojson" not in nominatim.call_args.kwargs["params"]


def test_nominatim_results_are_cached_per_query_and_geometry(nominatim):
    for _ in range(3):
        geo_utils.nominatim_search("באר שבע", polygon=True, limit=5)
    assert nominatim.call_count == 1


def test_nominatim_error_body_is_an_error(nominatim):
    nominatim.return_value.json.return_value = {"error": "Unable to geocode"}
    with pytest.raises(ValueError):
        geo_utils.nominatim_search("x", polygon=False, limit=5)


@pytest.mark.asyncio
async def test_nominatim_geocoder_wraps_failures(nominatim):
    nominatim.side_effect = geo_utils.requests.exceptions.ConnectionError("down")
    with pytest.raises(GeocoderError):
        await NominatimGeocoder().search("x", False, 5)


# --- Gateway get_geocode_address: schema-driven arguments ---------------------------------------------

def gateway_tool(props: dict, required=("q",), reply=None):
    schema = {"type": "object", "properties": props, "required": list(required)}
    tool = MagicMock()
    tool.name = "get_geocode_address"
    tool.args_schema = schema

    async def ainvoke(args):
        tool.last_args = args
        return reply if reply is not None else json.dumps({"ok": True, "data": [CITY], "error": None})

    tool.ainvoke = ainvoke
    return tool


@pytest.mark.parametrize("ptype, expected", [("string", "1"), ("integer", 1), ("boolean", True)])
def test_gateway_polygon_argument_follows_schema_type(ptype, expected):
    g = GatewayGeocoder(gateway_tool({"q": {"type": "string"}, "polygon_geojson": {"type": ptype}}))
    assert g.build_args("רפיח", polygon=True, limit=3) == {"q": "רפיח", "polygon_geojson": expected}
    assert g.build_args("רפיח", polygon=False, limit=3) == {"q": "רפיח"}


def test_gateway_sends_only_supported_parameters():
    g = GatewayGeocoder(gateway_tool({"address": {"type": "string"}, "limit": {"type": "integer"}}, required=["address"]))
    assert g.build_args("רפיח", polygon=True, limit=3) == {"address": "רפיח", "limit": 3}


def test_gateway_with_pydantic_schema_and_no_query_field():
    class Args(BaseModel):
        foo: int

    g = GatewayGeocoder(SimpleNamespace(name="get_geocode_address", args_schema=Args))
    with pytest.raises(GeocoderError):
        g.build_args("x", False, 1)


@pytest.mark.asyncio
async def test_gateway_is_preferred_and_parsed():
    tool = gateway_tool({"q": {"type": "string"}, "polygon_geojson": {"type": "string"}})
    fallback = FakeGeocoder({"באר שבע": city_results})
    res = await resolve("באר שבע", "area", "city", "beer_sheva", [GatewayGeocoder(tool), fallback])
    assert res.feature.provider == "gateway:get_geocode_address"
    assert tool.last_args == {"q": "באר שבע", "polygon_geojson": "1"}
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_failing_gateway_falls_back_once():
    tool = gateway_tool({"q": {"type": "string"}}, reply=json.dumps(
        {"ok": False, "data": None, "error": {"category": "EXTERNAL_TOOL_ERROR", "message": "503"}}))
    fallback = FakeGeocoder({"באר שבע": city_results})
    res = await resolve("באר שבע", "area", "city", "beer_sheva", [GatewayGeocoder(tool), fallback])
    assert res.feature.provider == "nominatim" and len(fallback.calls) == 1
    assert any("fell back to nominatim" in n and "503" in n for n in res.feature.notes)


@pytest.mark.asyncio
async def test_all_geocoders_down():
    res = await resolve("באר שבע", "area", "city", None, [FakeGeocoder(error="timeout")])
    assert res.feature is None and res.error[0] == "GEOCODER_UNAVAILABLE"


def test_build_geocoders_detects_the_gateway_tool():
    other = StructuredTool.from_function(func=lambda q: q, name="nominatim_helper", description="x")
    assert [g.name for g in build_geocoders([other])] == ["nominatim"]
    assert [g.name for g in build_geocoders([gateway_tool({"q": {"type": "string"}})])] == [
        "gateway:get_geocode_address", "nominatim"]


@pytest.mark.parametrize("payload", [
    {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": None}]},
    [CITY], {"results": [CITY]}, json.dumps([CITY]),
])
def test_provider_payload_shapes(payload):
    assert len(provider_items(payload)) == 1


@pytest.mark.parametrize("payload", [{"error": "Unable to geocode"}, {"weird": 1}, 42, "not json"])
def test_malformed_provider_payloads(payload):
    with pytest.raises(GeocoderError):
        provider_items(payload)


# --- Candidate parsing, type filtering, ambiguity ------------------------------------------------------

def test_jsonv2_center_and_bbox_are_lon_lat():
    c = normalize_candidate(CITY)
    assert c.center == CITY_CENTER                       # (lon, lat), not (lat, lon)
    min_lon, min_lat, max_lon, max_lat = c.bbox
    assert min_lon < CITY_CENTER[0] < max_lon and min_lat < CITY_CENTER[1] < max_lat
    assert c.provider_id == "relation/1382494" and c.english_name == "Beer Sheva"


def test_geojson_feature_candidate():
    feature = {"type": "Feature", "bbox": [34.7, 31.2, 34.9, 31.3],
               "properties": {"osm_type": "node", "osm_id": 9, "category": "place", "type": "city", "name": "x"},
               "geometry": {"type": "Point", "coordinates": [34.79, 31.25]}}
    c = normalize_candidate(feature)
    assert c.center == (34.79, 31.25) and c.bbox == (34.7, 31.2, 34.9, 31.3) and c.feature_class == "place:city"


@pytest.mark.asyncio
async def test_type_filter_skips_a_restaurant_named_like_the_city():
    res = await resolve("חיפה", "area", "city", "haifa", [FakeGeocoder({"חיפה": [RESTAURANT, HAIFA]})])
    assert res.feature.provider_id == "relation/1382495"


@pytest.mark.asyncio
async def test_no_candidate_of_the_expected_type():
    res = await resolve("חיפה", "area", "river", None, [FakeGeocoder({"חיפה": [RESTAURANT, HAIFA]})])
    assert res.feature is None and res.error[0] == "LOCATION_NOT_FOUND" and len(res.alternatives) == 2


@pytest.mark.asyncio
async def test_distant_similar_candidates_are_ambiguous_and_can_be_picked():
    a = item(1, "כפר כנא", "place", "town", 35.34, 32.75, importance=0.5)
    b = item(2, "כפר כנא", "place", "village", 35.50, 33.20, importance=0.48)
    geocoder = FakeGeocoder({"כפר כנא": [a, b]})
    res = await resolve("כפר כנא", "point", "town", "kafr_kanna", [geocoder])
    assert res.error[0] == "LOCATION_AMBIGUOUS" and {x["provider_id"] for x in res.alternatives} == {
        "relation/1", "relation/2"}
    picked = await resolve("כפר כנא", "point", "town", "kafr_kanna", [geocoder], provider_id="relation/2")
    assert picked.feature.center == (35.50, 33.20)


@pytest.mark.asyncio
async def test_close_or_unequal_candidates_are_not_ambiguous():
    a = item(1, "x", "place", "town", 35.34, 32.75, importance=0.9)
    b = item(2, "x", "place", "town", 35.50, 33.20, importance=0.3)
    res = await resolve("x", "point", "town", None, [FakeGeocoder({"x": [a, b]})])
    assert res.feature.provider_id == "relation/1"


@pytest.mark.asyncio
async def test_no_match():
    res = await resolve("אין כזה", "area", "city", None, [FakeGeocoder({})])
    assert res.error == ("LOCATION_NOT_FOUND", "'אין כזה': no match")


# --- Geometry: point vs polygon, line, validity, size ---------------------------------------------------

@pytest.mark.asyncio
async def test_point_need_does_not_request_a_polygon():
    geocoder = FakeGeocoder({"באר שבע": city_results})
    res = await resolve("באר שבע", "point", "city", "beer_sheva", [geocoder])
    assert geocoder.calls == [("באר שבע", False, settings.GEO_CANDIDATE_LIMIT)]
    assert res.geometry["geometry_type"] == "Point"
    assert shapely.from_wkt(res.geometry["wkt"]).coords[0] == pytest.approx(CITY_CENTER)


@pytest.mark.asyncio
async def test_area_need_keeps_the_polygon_and_center():
    geocoder = FakeGeocoder({"באר שבע": city_results})
    res = await resolve("באר שבע", "area", "city", "beer_sheva", [geocoder])
    assert geocoder.calls[0][1] is True
    assert res.geometry["geometry_type"] == "Polygon" and res.feature.center == CITY_CENTER
    assert res.geometry["token"].endswith("_polygon_wkt") and "beer_sheva" in res.geometry["token"]
    assert res.limitation is None


@pytest.mark.asyncio
async def test_point_is_never_presented_as_an_area():
    res = await resolve("מחנה", "area", None, None, [FakeGeocoder({"מחנה": [item(5, "מחנה", "place", "hamlet", 35, 32, osm_type="node")]})])
    assert res.feature.geometry_type == "Point" and "no area geometry" in res.limitation
    assert res.geometry["derivation"] == "provider representative point"


@pytest.mark.asyncio
async def test_river_line_is_kept_as_a_line():
    res = await resolve("נחל קישון", "line", "river", "kishon_river", [FakeGeocoder({"נחל קישון": [RIVER]})])
    assert res.geometry["geometry_type"] == "LineString" and res.limitation is None


@pytest.mark.asyncio
async def test_malformed_geometry_is_reported():
    bad = item(9, "x", "boundary", "administrative", 35, 32)
    bad["geojson"] = {"type": "Polygon", "coordinates": [[[35, 32], [35.1, 32]]]}
    res = await resolve("x", "area", "region", None, [FakeGeocoder({"x": [bad]})])
    assert res.error[0] == "MALFORMED_GEOMETRY"


def test_invalid_bowtie_is_repaired_with_a_note():
    geom, notes = parse_geojson({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]})
    assert geom.is_valid and geom.geom_type in {"Polygon", "MultiPolygon"} and "repaired" in notes[0]


def test_out_of_range_coordinates_are_rejected():
    with pytest.raises(GeometryError):
        parse_geojson({"type": "Point", "coordinates": [32.0, 134.0]})


def test_simplification_is_bounded_and_never_a_bbox():
    circle = shapely.Point(35, 32).buffer(0.05, quad_segs=256)  # ~1000 vertices
    wkt, tol = to_wkt(circle, max_chars=4000, max_tolerance_m=100)
    simplified = shapely.from_wkt(wkt)
    assert len(wkt) <= 4000 and tol is not None and tol * 111_195 <= 100
    assert simplified.geom_type == "Polygon" and len(simplified.exterior.coords) > 5  # not an envelope
    assert circle.symmetric_difference(simplified).area / circle.area < 0.01
    with pytest.raises(GeometryError):
        to_wkt(circle, max_chars=300, max_tolerance_m=1)


@pytest.mark.asyncio
async def test_too_complex_geometry_is_an_error(monkeypatch):
    monkeypatch.setattr(settings, "GEO_MAX_WKT_CHARS", 200)
    monkeypatch.setattr(settings, "GEO_MAX_SIMPLIFY_TOLERANCE_M", 1)
    circle = shapely.Point(35, 32).buffer(0.05, quad_segs=256)
    big = item(9, "x", "boundary", "administrative", 35, 32, geometry=circle)
    res = await resolve("x", "area", "region", None, [FakeGeocoder({"x": [big]})])
    assert res.error[0] == "GEOMETRY_TOO_COMPLEX"


# --- resolve_location tool: registry, dedupe, cached failures -------------------------------------------

class Thread:
    """Minimal ToolRuntime stand-in that applies Command updates to its state."""

    def __init__(self):
        self.state = {"messages": []}
        self.n = 0

    def runtime(self):
        self.n += 1
        return SimpleNamespace(state=self.state, tool_call_id=f"call-{self.n}", context=None)

    async def call(self, tool, **kwargs):
        out = await tool.coroutine(**kwargs, runtime=self.runtime())
        if hasattr(out, "update"):
            self.state["geo_registry"] = merge_geo_registry(self.state.get("geo_registry"), out.update["geo_registry"])
            return json.loads(out.update["messages"][0].content)
        return json.loads(out)


@pytest.mark.asyncio
async def test_tool_dedupes_within_a_thread_and_keeps_geometry_out_of_output():
    geocoder = FakeGeocoder({"באר שבע": city_results})
    tool = make_resolve_location_tool([geocoder])
    thread = Thread()
    first = await thread.call(tool, canonical_hebrew="באר שבע", geometry="area", entity_type="city", english_name="beer_sheva")
    second = await thread.call(tool, canonical_hebrew="באר שבע", geometry="area", entity_type="city", english_name="beer_sheva")
    assert len(geocoder.calls) == 1 and second["data"]["cached"] is True
    assert first["data"]["geometry_types"] == ["Polygon"] and "POLYGON" not in json.dumps(first)
    ref = first["data"]["feature_ref"]

    # The same feature resolved as a point keeps both geometries.
    await thread.call(tool, canonical_hebrew="באר שבע", geometry="point", entity_type="city", english_name="beer_sheva")
    feature = thread.state["geo_registry"]["features"][ref]
    assert set(feature["geometries"]) == {"Polygon", "Point"}
    token = feature["geometries"]["Polygon"]
    sql = substitute_geometries(f"SELECT 1 WHERE ST_Contains(ST_GeometryFromText('@{token}@'), ST_Point(1, 2))",
                                thread.state["geo_registry"])
    assert "'POLYGON ((" in sql and "@" not in sql


@pytest.mark.asyncio
async def test_tool_caches_failures_instead_of_retrying():
    geocoder = FakeGeocoder(error="timeout")
    tool = make_resolve_location_tool([geocoder])
    thread = Thread()
    first = await thread.call(tool, canonical_hebrew="רפיח", geometry="area")
    second = await thread.call(tool, canonical_hebrew="רפיח", geometry="area")
    assert first["error"]["category"] == "GEOCODER_UNAVAILABLE" and second["error"]["cached"] is True
    assert len(geocoder.calls) == 1


@pytest.mark.asyncio
async def test_tool_retries_once_with_the_english_name():
    geocoder = FakeGeocoder({"beer sheva": city_results})
    tool = make_resolve_location_tool([geocoder])
    out = await Thread().call(tool, canonical_hebrew="באר-שבע העתיקה", geometry="area", entity_type="city",
                              english_name="beer_sheva")
    assert out["ok"] and [c[0] for c in geocoder.calls] == ["באר-שבע העתיקה", "beer sheva"]
    assert any("English name" in n for n in out["data"]["notes"])


def test_unknown_placeholder_is_rejected():
    with pytest.raises(ValueError, match="unknown geometry placeholder"):
        substitute_geometries("SELECT '@nowhere_wkt@'", {})
    assert substitute_geometries("SELECT 1", {}) == "SELECT 1"
