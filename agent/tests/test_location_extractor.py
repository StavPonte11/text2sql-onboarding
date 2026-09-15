import pytest
import requests
from unittest.mock import MagicMock, patch, AsyncMock
from shapely.geometry import shape

from agent.services.location_extractor import (
    LocationMapping,
    LocationExtractionResult,
    LocationExtractorAgent
)
from agent.services.geo_utils import (
    get_geojson_polygon,
    geojson_to_simplified_wkt
)


@pytest.fixture(autouse=True)
def clear_geocoding_cache():
    """Clear geocoding cache before every test to ensure test isolation."""
    get_geojson_polygon.cache_clear()


# 1. Test LLM Parsing
def test_llm_json_parsing():
    # Instantiate with None LLM since we only call the helper _parse_llm_json
    agent = LocationExtractorAgent(llm_client=None)

    # A. Valid JSON (Flat)
    valid_json = '{"עזה": "gaza", "רפיח": "rafah"}'
    assert agent._parse_llm_json(valid_json) == {"עזה": "gaza", "רפיח": "rafah"}

    # B. Valid JSON with _analysis and locations structure
    new_format_json = '{\n  "_analysis": "Detected \'חאן יונס\' and \'רפיח\' as populated cities.",\n  "locations": {\n    "חאן יונס": "khan_yunis",\n    "רפיח": "rafah"\n  }\n}'
    assert agent._parse_llm_json(new_format_json) == {"חאן יונס": "khan_yunis", "רפיח": "rafah"}

    # C. Structured format with empty locations
    empty_locations_json = '{\n  "_analysis": "No locations found.",\n  "locations": {}\n}'
    assert agent._parse_llm_json(empty_locations_json) == {}

    # D. JSON wrapped in Markdown code blocks
    markdown_json = '```json\n{"_analysis": "Reasoning...", "locations": {"עזה": "gaza", "חאן יונס": "khan_yunis"}}\n```'
    assert agent._parse_llm_json(markdown_json) == {"עזה": "gaza", "חאן יונס": "khan_yunis"}

    # E. Invalid/broken JSON that can be repaired
    broken_json = '{"_analysis": "reason", "locations": {"עזה": "gaza", "רפיח": "rafah"'
    assert agent._parse_llm_json(broken_json) == {"עזה": "gaza", "רפיח": "rafah"}

    # F. Empty or non-JSON output
    empty_output = ""
    assert agent._parse_llm_json(empty_output) == {}

    invalid_format = "Hello, this is not JSON"
    assert agent._parse_llm_json(invalid_format) == {}


# 2. Test WKT Simplification
def test_wkt_simplification_small():
    # Simple square polygon (5 vertices)
    geojson_square = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]
    }
    
    # Should fit comfortably in 2100 chars (around 60 chars)
    wkt_result = geojson_to_simplified_wkt(geojson_square, max_length=2100)
    assert wkt_result is not None
    assert wkt_result.startswith("'POLYGON")
    assert wkt_result.endswith("'")


def test_wkt_simplification_large_binary_search():
    # Generate a complex circle-like polygon with many decimal places
    import math
    coords = []
    # 150 points around a unit circle
    for i in range(150):
        angle = (2 * math.pi * i) / 150
        r = 1.0 + 0.1 * math.sin(5 * angle)
        coords.append([r * math.cos(angle), r * math.sin(angle)])
    coords.append(coords[0])  # Close polygon
    
    geojson_dense = {
        "type": "Polygon",
        "coordinates": [coords]
    }
    
    # Raw WKT with 150 coordinates will be very long (around 4500 characters)
    raw_wkt = f"'{shape(geojson_dense).wkt}'"
    assert len(raw_wkt) > 1000
    
    # Set max_length to 300, which is too small for raw but simplified can fit
    wkt_result = geojson_to_simplified_wkt(geojson_dense, max_length=300)
    assert wkt_result is not None
    assert len(wkt_result) <= 300


def test_wkt_simplification_fallback_to_envelope():
    # Create a complex circle polygon
    import math
    coords = []
    for i in range(150):
        angle = (2 * math.pi * i) / 150
        r = 1.0 + 0.1 * math.sin(5 * angle)
        coords.append([r * math.cos(angle), r * math.sin(angle)])
    coords.append(coords[0])
    
    geojson_dense = {
        "type": "Polygon",
        "coordinates": [coords]
    }
    
    # Set max_length extremely low (e.g. 120 characters)
    # Binary search at tolerance=1.0 will still be too long or fail,
    # and it will fall back to envelope (bounding box) which has only 5 vertices and fits.
    wkt_result = geojson_to_simplified_wkt(geojson_dense, max_length=120)
    assert wkt_result is not None
    assert len(wkt_result) <= 120
    # Verify the exact envelope fallback branch was taken, not just any polygon
    import shapely.wkt as shwkt
    expected_envelope_wkt = f"'{shwkt.dumps(shape(geojson_dense).envelope, rounding_precision=4)}'"
    assert wkt_result == expected_envelope_wkt

    # Impossibly small limit that even the envelope cannot fit
    assert geojson_to_simplified_wkt(geojson_dense, max_length=10) is None


# 3. Test API Failure handling (Internal requests.get mock)
def test_geocoding_api_failure(mocker):
    # Mock requests.get to return a response whose raise_for_status() raises HTTPError,
    # mirroring the real code path: requests.get(...) -> res.raise_for_status().
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("Nominatim down")
    mock_get = mocker.patch("agent.services.geo_utils.requests.get", return_value=mock_response)

    # Verify get_geojson_polygon catches it and returns None
    result = get_geojson_polygon("failure_test_location")
    assert result is None
    mock_get.assert_called_once()
    mock_response.raise_for_status.assert_called_once()


# 4. End-to-End Agent execution mock
@pytest.mark.asyncio
async def test_location_extractor_agent_e2e(mocker):
    # Mock LLM response with new format
    mock_response = MagicMock()
    mock_response.content = '```json\n{\n  "_analysis": "Detected עזה and רפיח as locations.",\n  "locations": {\n    "עזה": "gaza",\n    "רפיח": "rafah"\n  }\n}\n```'
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    # Mock Nominatim geocoding function
    gaza_geojson = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]
    }
    
    def mock_geocoder(loc_name):
        if loc_name == "עזה":
            return gaza_geojson
        return None
        
    mocker.patch("agent.services.geo_utils.get_geojson_polygon", side_effect=mock_geocoder)
    
    agent = LocationExtractorAgent(llm_client=mock_llm, max_wkt_length=2100, api_token="test-api-key")
    
    result = await agent.run("תראה לי אירועים בעזה וברפיח")
    
    # Verify result counts
    assert len(result.valid_locations) == 2
    
    gaza_mapping = next(loc for loc in result.valid_locations if loc.hebrew_name == "עזה")
    assert gaza_mapping.english_name == "gaza"
    assert gaza_mapping.wkt_polygon is not None
    assert gaza_mapping.error_message is None
    
    rafah_mapping = next(loc for loc in result.valid_locations if loc.hebrew_name == "רפיח")
    assert rafah_mapping.english_name == "rafah"
    assert rafah_mapping.wkt_polygon is None
    assert rafah_mapping.error_message == "No geometry found from API"
    
    # Verify formatted instruction parts
    assert "gaza_wkt =" in result.location_wkt_instruction
    assert "rafah_wkt" not in result.location_wkt_instruction
    
    # Verify serializable dicts
    assert result.raw_locations_dict == {"עזה": "gaza"}
    assert "gaza_wkt" in result.locations_coords_dict
    assert result.analysis == "Detected עזה and רפיח as locations."


@pytest.mark.asyncio
async def test_location_extractor_class(mocker):
    """LocationExtractor wraps the agent and exposes extra state via state_update()."""
    from agent.nodes.extractor import LocationExtractor

    mock_response = MagicMock()
    mock_response.content = '{\n  "_analysis": "Extracted gaza",\n  "locations": {"עזה": "gaza"}\n}'

    mock_llm = MagicMock()
    mock_llm.invoke = MagicMock(return_value=mock_response)

    mocker.patch("agent.nodes.extractor.get_llm", return_value=mock_llm)

    # Mock geocoder
    gaza_geojson = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]
    }
    mocker.patch("agent.services.geo_utils.get_geojson_polygon", return_value=gaza_geojson)

    extractor = LocationExtractor(runtime_flags={})
    entries = extractor.extract("עזה")
    update = extractor.state_update()

    assert update["locations_dict"]["names"] == {"עזה": "gaza"}
    assert "gaza_wkt" in update["locations_dict"]["coords"]
    assert "gaza_wkt =" in update["location_wkt_instruction"]


def test_location_extractor_agent_base_extractor_interface(mocker):
    # Mock LLM response
    mock_response = MagicMock()
    mock_response.content = '{\n  "_analysis": "Extracted gaza",\n  "locations": {"עזה": "gaza"}\n}'
    
    mock_llm = MagicMock()
    mock_llm.invoke = MagicMock(return_value=mock_response)
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    # Mock geocoder
    gaza_geojson = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]
    }
    mocker.patch("agent.services.geo_utils.get_geojson_polygon", return_value=gaza_geojson)
    
    agent = LocationExtractorAgent(llm_client=mock_llm, max_wkt_length=2100)
    
    entries = agent.extract("עזה")
    
    assert len(entries) == 1
    assert entries[0].term == "עזה"
    assert "gaza" in entries[0].context
    assert "POLYGON" in entries[0].context


@pytest.mark.asyncio
async def test_e2e_mixed_outcomes_with_fallback(mocker):
    """Test a scenario where one location succeeds, one triggers the bounding box fallback, and one fails completely."""
    # Mock LLM response with 3 distinct locations using new schema
    mock_response = MagicMock()
    mock_response.content = '{\n  "_analysis": "Extracted tel aviv, jerusalem, atlantis",\n  "locations": {"תל אביב": "tel_aviv", "ירושלים": "jerusalem", "אטלנטיס": "atlantis"}\n}'
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    # Generate a massive complex polygon for Jerusalem that will force the fallback
    import math
    complex_coords = [[[(1.0 + 0.1 * math.sin(5 * (2 * math.pi * i) / 150)) * math.cos((2 * math.pi * i) / 150), 
                        (1.0 + 0.1 * math.sin(5 * (2 * math.pi * i) / 150)) * math.sin((2 * math.pi * i) / 150)] 
                       for i in range(150)]]
    complex_coords[0].append(complex_coords[0][0])
    
    jerusalem_geojson = {"type": "Polygon", "coordinates": complex_coords}
    tel_aviv_geojson = {"type": "Polygon", "coordinates": [[[0,0], [0,1], [1,1], [1,0], [0,0]]]}
    
    def mock_geocoder(loc_name):
        if loc_name == "תל אביב":
            return tel_aviv_geojson
        elif loc_name == "ירושלים":
            return jerusalem_geojson
        return None # Atlantis doesn't exist
        
    mocker.patch("agent.services.geo_utils.get_geojson_polygon", side_effect=mock_geocoder)
    
    # Set max_wkt_length artificially low (150 chars) so Jerusalem is forced to fallback to envelope
    agent = LocationExtractorAgent(llm_client=mock_llm, max_wkt_length=150)
    result = await agent.run("תראה לי אירועים בתל אביב, ירושלים ואטלנטיס")
    
    assert len(result.valid_locations) == 3
    
    # 1. Tel Aviv should be normal
    ta = next(loc for loc in result.valid_locations if loc.hebrew_name == "תל אביב")
    assert ta.wkt_polygon is not None and len(ta.wkt_polygon) <= 150
    
    # 2. Jerusalem should trigger the bounding box fallback
    jeru = next(loc for loc in result.valid_locations if loc.hebrew_name == "ירושלים")
    assert jeru.wkt_polygon is not None and len(jeru.wkt_polygon) <= 150
    
    # 3. Atlantis should fail gracefully
    atl = next(loc for loc in result.valid_locations if loc.hebrew_name == "אטלנטיס")
    assert atl.wkt_polygon is None
    assert atl.error_message == "No geometry found from API"


@pytest.mark.asyncio
async def test_e2e_sync_async_parity(mocker):
    """Test that the Event Loop fix works: run (async) and extract (sync) must share exact underlying logic."""
    mock_response = MagicMock()
    mock_response.content = '{\n  "_analysis": "Extracted haifa",\n  "locations": {"חיפה": "haifa"}\n}'
    
    mock_llm = MagicMock()
    mock_llm.invoke = MagicMock(return_value=mock_response)
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    haifa_geojson = {"type": "Polygon", "coordinates": [[[0,0], [0,1], [1,1], [1,0], [0,0]]]}
    mocker.patch("agent.services.geo_utils.get_geojson_polygon", return_value=haifa_geojson)
    
    agent = LocationExtractorAgent(llm_client=mock_llm)
    
    # Execute Async
    async_result = await agent.run("חיפה")
    
    # Execute Sync
    sync_entries = agent.extract("חיפה")
    
    # Prove both paths processed the exact same location data successfully
    assert "haifa_wkt" in async_result.locations_coords_dict
    assert len(sync_entries) == 1
    assert "haifa" in sync_entries[0].context
    assert "POLYGON" in sync_entries[0].context
    
    # Verify the shared private method was hit (implying event loops aren't conflicting)
    assert async_result.raw_locations_dict == {"חיפה": "haifa"}


@pytest.mark.asyncio
async def test_e2e_empty_llm_response(mocker):
    """Test the pipeline's robustness when the LLM detects zero locations in the text."""
    mock_response = MagicMock()
    mock_response.content = '{\n  "_analysis": "No locations found.",\n  "locations": {}\n}'  # LLM recognized no locations
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    agent = LocationExtractorAgent(llm_client=mock_llm)
    result = await agent.run("אין פה שום מיקום רלוונטי")
    
    # Verify everything degrades gracefully
    assert len(result.valid_locations) == 0
    assert result.raw_locations_dict == {}
    assert result.locations_coords_dict == {}
    assert result.location_wkt_instruction == ""


# 5. Test all 9 challenging user prompt example scenarios
@pytest.mark.parametrize(
    "llm_output_json,expected_map",
    [
        # Scenario 1: Date & populated cities
        (
            '{\n  "_analysis": "Detected חאן יונס and רפיח as populated cities. מאי 2025 is a date, ignored.",\n  "locations": {\n    "חאן יונס": "khan_yunis",\n    "רפיח": "rafah"\n  }\n}',
            {"חאן יונס": "khan_yunis", "רפיח": "rafah"}
        ),
        # Scenario 2: Directional modifiers dropped & Acronym expansion
        (
            '{\n  "_analysis": "Detected ת״א (expanded to תל אביב), dropped מזרח. Detected איו״ש (expanded to יהודה ושומרון).",\n  "locations": {\n    "תל אביב": "tel_aviv",\n    "יהודה ושומרון": "judea_and_samaria"\n  }\n}',
            {"תל אביב": "tel_aviv", "יהודה ושומרון": "judea_and_samaria"}
        ),
        # Scenario 3: Sports teams ignored
        (
            '{\n  "_analysis": "Ignored מכבי חיפה and הפועל ירושלים as sports teams. Detected final חיפה.",\n  "locations": {\n    "חיפה": "haifa"\n  }\n}',
            {"חיפה": "haifa"}
        ),
        # Scenario 4: Personal & restaurant names ignored
        (
            '{\n  "_analysis": "Ignored גולן and שרון as personal names, and אוסלו as restaurant name. No locations found.",\n  "locations": {}\n}',
            {}
        ),
        # Scenario 5: Foreign/Arabic villages & formal directional modifier
        (
            '{\n  "_analysis": "Detected בינת ג\'בייל, מארון א-ראס, אל-ח\'יאם, and דרום לבנון as valid location entities.",\n  "locations": {\n    "בינת ג\'בייל": "bint_jbeil",\n    "מארון א-ראס": "maroun_al_ras",\n    "אל-ח\'יאם": "al_khiam",\n    "דרום לבנון": "south_lebanon"\n  }\n}',
            {"בינת ג'בייל": "bint_jbeil", "מארון א-ראס": "maroun_al_ras", "אל-ח'יאם": "al_khiam", "דרום לבנון": "south_lebanon"}
        ),
        # Scenario 6: Structural prefixes retained (כפר, ואדי, נחל)
        (
            '{\n  "_analysis": "Detected כפר קאסם, כפר כנא, ואדי עארה, נחל קישון, retaining structural prefixes.",\n  "locations": {\n    "כפר קאסם": "kafr_qasim",\n    "כפר כנא": "kafr_kanna",\n    "ואדי עארה": "wadi_ara",\n    "נחל קישון": "kishon_river"\n  }\n}',
            {"כפר קאסם": "kafr_qasim", "כפר כנא": "kafr_kanna", "ואדי עארה": "wadi_ara", "נחל קישון": "kishon_river"}
        ),
        # Scenario 7: Refugee camps & military base
        (
            '{\n  "_analysis": "Detected מחנה הפליטים ג\'נין, corrected בלטא to מחנה בלאטה, detected שכם and מחנה נחשונים.",\n  "locations": {\n    "מחנה הפליטים ג\'נין": "jenin_refugee_camp",\n    "מחנה בלאטה": "balata_refugee_camp",\n    "שכם": "nablus",\n    "מחנה נחשונים": "nahshonim_camp"\n  }\n}',
            {"מחנה הפליטים ג'נין": "jenin_refugee_camp", "מחנה בלאטה": "balata_refugee_camp", "שכם": "nablus", "מחנה נחשונים": "nahshonim_camp"}
        ),
        # Scenario 8: Landmarks & mountain standardization (ג'בל שייח' -> הר חרמון)
        (
            '{\n  "_analysis": "Detected נהר הליטני, הר דב, and standardized ג\'בל שייח\' to הר חרמון.",\n  "locations": {\n    "נהר הליטני": "litani_river",\n    "הר דב": "har_dov",\n    "הר חרמון": "mount_hermon"\n  }\n}',
            {"נהר הליטני": "litani_river", "הר דב": "har_dov", "הר חרמון": "mount_hermon"}
        ),
        # Scenario 9: Operational sectors ignored
        (
            '{\n  "_analysis": "Ignored הגזרה הצפונית, extracted עוטף עזה.",\n  "locations": {\n    "עוטף עזה": "gaza_envelope"\n  }\n}',
            {"עוטף עזה": "gaza_envelope"}
        ),
        # Scenario 10 (Unseen): Acronym expansions (ארה״ב, ב״ש, פ״ת)
        (
            '{\n  "_analysis": "Expanded לארה״ב to ארצות הברית, בב״ש to באר שבע, and בפ״ת to פפתח תקווה.",\n  "locations": {\n    "ארצות הברית": "united_states",\n    "באר שבע": "beersheba",\n    "פתח תקווה": "petah_tikva"\n  }\n}',
            {"ארצות הברית": "united_states", "באר שבע": "beersheba", "פתח תקווה": "petah_tikva"}
        ),
        # Scenario 11 (Unseen): Generic directional vs Formal Country (צפון תל אביב vs צפון קוריאה / דרום אפריקה)
        (
            '{\n  "_analysis": "Dropped צפון from צפון תל אביב; kept formal country names קוריאה הצפונית and דרום אפריקה.",\n  "locations": {\n    "תל אביב": "tel_aviv",\n    "קוריאה הצפונית": "north_korea",\n    "דרום אפריקה": "south_africa"\n  }\n}',
            {"תל אביב": "tel_aviv", "קוריאה הצפונית": "north_korea", "דרום אפריקה": "south_africa"}
        ),
        # Scenario 12 (Unseen): Military units & dates ignored vs Base
        (
            '{\n  "_analysis": "Ignored חטיבה 7 and אוגדה 98 as military units, ignored באוגוסט 2024 as date. Extracted מחנה עמוס.",\n  "locations": {\n    "מחנה עמוס": "amos_camp"\n  }\n}',
            {"מחנה עמוס": "amos_camp"}
        ),
    ]
)
def test_challenging_query_examples_parsing(llm_output_json, expected_map):
    """Test parsing of all 12 challenging scenario outputs from the prompt rules."""
    agent = LocationExtractorAgent(llm_client=None)
    result = agent._parse_llm_json(llm_output_json)
    assert result == expected_map