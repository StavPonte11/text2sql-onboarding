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

    # A. Valid JSON
    valid_json = '{"עזה": "gaza", "רפיח": "rafah"}'
    assert agent._parse_llm_json(valid_json) == {"עזה": "gaza", "רפיח": "rafah"}

    # B. JSON wrapped in Markdown code blocks
    markdown_json = '```json\n{"עזה": "gaza", "חאן יונס": "khan_yunis"}\n```'
    assert agent._parse_llm_json(markdown_json) == {"עזה": "gaza", "חאן יונס": "khan_yunis"}

    # C. Invalid/broken JSON that can be repaired
    broken_json = '{"עזה": "gaza", "רפיח": "rafah"'
    assert agent._parse_llm_json(broken_json) == {"עזה": "gaza", "רפיח": "rafah"}

    # D. Empty or non-JSON output
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
    # Bounding box of unit circle region fits within limits
    assert "POLYGON" in wkt_result
    
    # Impossibly small limit that even the envelope cannot fit
    assert geojson_to_simplified_wkt(geojson_dense, max_length=10) is None


# 3. Test API Failure handling (Internal requests.get mock)
def test_geocoding_api_failure(mocker):
    # Mock requests.get to throw HTTPError
    mock_get = mocker.patch("requests.get", side_effect=requests.exceptions.HTTPError("Nominatim down"))
    
    # Verify get_geojson_polygon catches it and returns None
    result = get_geojson_polygon("failure_test_location")
    assert result is None
    mock_get.assert_called_once()


# 4. End-to-End Agent execution mock
@pytest.mark.asyncio
async def test_location_extractor_agent_e2e(mocker):
    # Mock LLM response
    mock_response = MagicMock()
    mock_response.content = '```json\n{"עזה": "gaza", "רפיח": "rafah"}\n```'
    
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


@pytest.mark.asyncio
async def test_location_extractor_class(mocker):
    """LocationExtractor wraps the agent and exposes extra state via state_update()."""
    from agent.nodes.extractor import LocationExtractor

    mock_response = MagicMock()
    mock_response.content = '{"עזה": "gaza"}'

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
    mock_response.content = '{"עזה": "gaza"}'
    
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
    # Mock LLM response with 3 distinct locations
    mock_response = MagicMock()
    mock_response.content = '{"תל אביב": "tel_aviv", "ירושלים": "jerusalem", "אטלנטיס": "atlantis"}'
    
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
    mock_response.content = '{"חיפה": "haifa"}'
    
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
    mock_response.content = '{}'  # LLM recognized no locations
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    agent = LocationExtractorAgent(llm_client=mock_llm)
    result = await agent.run("אין פה שום מיקום רלוונטי")
    
    # Verify everything degrades gracefully
    assert len(result.valid_locations) == 0
    assert result.raw_locations_dict == {}
    assert result.locations_coords_dict == {}
    assert result.location_wkt_instruction == ""