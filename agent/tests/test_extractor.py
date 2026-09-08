import datetime
import pytest
from unittest.mock import patch, MagicMock

from agent.nodes.extractor import (
    TimeExtractor,
    HTTPExtractor,
    ContextEntry,
    extractor_node
)

def test_time_extractor():
    extractor = TimeExtractor()
    # Mock datetime.now
    with patch("agent.nodes.extractor.datetime") as mock_datetime:
        fixed_now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        mock_datetime.datetime.now.return_value = fixed_now
        mock_datetime.timedelta = datetime.timedelta
        
        entries = extractor.extract("what happened today?")
        
        # Should have only the current time anchor
        assert len(entries) == 1
        assert entries[0].term == "current_time"
        assert "2025-01-01T12:00:00" in entries[0].context

@patch("agent.nodes.extractor.requests.post")
def test_http_extractor_success(mock_post):
    extractor = HTTPExtractor("http://test-url", "test-extractor")
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "enrichments": [
            {"term": "test", "context": "test context"}
        ]
    }
    mock_post.return_value = mock_response
    
    entries = extractor.extract("test query")
    
    assert len(entries) == 1
    assert entries[0].term == "test"
    assert entries[0].term == "test"
    assert entries[0].context == "test context"
    mock_post.assert_called_once_with("http://test-url", json={"query": "test query", "runtime_flags": {}}, timeout=50)

@patch("agent.nodes.extractor.requests.post")
def test_http_extractor_failure(mock_post):
    extractor = HTTPExtractor("http://test-url", "test-extractor")
    mock_post.side_effect = Exception("Connection error")
    
    entries = extractor.extract("test query")
    
    assert len(entries) == 0
    mock_post.assert_called_once()

@patch("agent.nodes.extractor.TimeExtractor")
@patch("agent.nodes.extractor.LocationExtractor")
@patch("agent.nodes.extractor.HTTPExtractor")
def test_extractor_node(MockHTTPExtractor, MockLocationExtractor, MockTimeExtractor):
    # Setup mocks
    mock_time_ext = MagicMock()
    mock_time_ext.extract.return_value = [ContextEntry(term="time", context="time context")]
    mock_time_ext.state_update.return_value = {}
    MockTimeExtractor.return_value = mock_time_ext
    
    mock_loc_ext = MagicMock()
    mock_loc_ext.extract.return_value = [ContextEntry(term="location", context="location context")]
    mock_loc_ext.state_update.return_value = {"location_wkt_instruction": "test_instruction"}
    MockLocationExtractor.return_value = mock_loc_ext
    
    mock_http_ext = MagicMock()
    mock_http_ext.extract.return_value = [ContextEntry(term="http", context="http context")]
    mock_http_ext.state_update.return_value = {}
    MockHTTPExtractor.return_value = mock_http_ext
    
    state = {
        "user_query": "test query",
        "active_extractors": [{"name": "test-http", "url": "http://test-url"}],
        # Provide default values for other AgentState fields just in case
        "messages": [],
        "query_enrichments": [],
        "schema_plan": "",
        "sql_query": "",
        "trino_error": None,
        "refinement_count": 0,
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "feedback": None,
        "feedback_route": None,
        "non_interactive": None
    }
    
    result = extractor_node(state)
    
    enrichments = result.get("query_enrichments", [])
    assert len(enrichments) == 3
    terms = [e["term"] for e in enrichments]
    assert "time" in terms
    assert "location" in terms
    assert "http" in terms
    assert result.get("location_wkt_instruction") == "test_instruction"
