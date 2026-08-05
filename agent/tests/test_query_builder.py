import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agent.nodes.query_builder import query_builder_node, _build_feedback_and_enrichments_str
from agent.state import AgentState


def test_build_feedback_and_enrichments_deduplication():
    feedback = "Don't use LIMIT 1"
    loaded_skills = []
    enrichments = [
        {"term": "current_time", "context": "The current time is 2026-08-05T12:00:00"},
        {"term": "צרפת", "context": "Location 'צרפת' translated to 'France' with polygon: POLYGON((...))"},
        {"term": "MDA", "context": "Magen David Adom"},
    ]

    # When location_instruction is provided, polygon entries are filtered from feedback_str
    result_with_loc = _build_feedback_and_enrichments_str(
        feedback=feedback,
        loaded_skills=loaded_skills,
        enrichments=enrichments,
        has_location_instruction=True,
    )

    assert "Don't use LIMIT 1" in result_with_loc
    assert "• Current Time: The current time is 2026-08-05T12:00:00" in result_with_loc
    assert "• MDA: Magen David Adom" in result_with_loc
    assert "POLYGON" not in result_with_loc
    assert "צרפת" not in result_with_loc


@pytest.mark.asyncio
async def test_query_builder_node_sql_output(mock_langfuse, mock_llm):
    state: AgentState = {
        "user_query": "הצג את כל הטיסות מעל צרפת",
        "jeen_catalog": "Table: flights (id, geom)",
        "location_wkt_instruction": "France polygon is @polygon_france@",
        "query_enrichments": [
            {"term": "current_time", "context": "2026-08-05T12:00:00"},
            {"term": "צרפת", "context": "Location 'צרפת' translated to 'France' with polygon: POLYGON((1 1, 2 2))"},
        ],
        "feedback": None,
        "loaded_skills": None,
        "runtime_flags": {},
        "sql_query": "",
        "execution_path": [],
        "messages": [],
        "trino_error": None,
        "refinement_count": 0,
        "allowed_tables": None,
        "allowed_statuses": None,
        "rejection_category": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "active_skills": None,
        "last_error": None,
        "esca_write_failed": False,
        "error_history": None,
        "schema_explorer_retry_count": 0,
        "scoping_mode": "hybrid",
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
    }

    mock_response = MagicMock()
    mock_response.content = "```sql\nSELECT id FROM flights WHERE ST_Contains(ST_GeometryFromText(@polygon_france@), geom);\n```"
    mock_response.additional_kwargs = {"reasoning_content": "Decomposed request: 1. Fetch flights 2. Spatial filter"}

    with patch("agent.nodes.query_builder.ChatPromptTemplate.from_messages") as mock_from_messages:
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_response)
        mock_from_messages.return_value.__or__.return_value = mock_chain

        res = await query_builder_node(state)

        assert res["sql_query"] == "SELECT id FROM flights WHERE ST_Contains(ST_GeometryFromText(@polygon_france@), geom)"
        assert res["sql_explanation"] == "Decomposed request: 1. Fetch flights 2. Spatial filter"
        assert res["execution_path"] == ["query_builder"]

        call_args = mock_chain.ainvoke.call_args[0][0]
        assert call_args["jeen_catalog"] == "Table: flights (id, geom)"
        assert call_args["user_query"] == "הצג את כל הטיסות מעל צרפת"
        assert call_args["location_wkt_instruction"] == "France polygon is @polygon_france@"
        # Check feedback_str contains time but not the duplicated polygon
        assert "• Current Time: 2026-08-05T12:00:00" in call_args["feedback_str"]
        assert "POLYGON" not in call_args["feedback_str"]
