import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agent.nodes.finalizer import finalizer_node, get_esca_preview
from agent.state import AgentState


@pytest.mark.asyncio
async def test_finalizer_node_with_inline_results(mock_langfuse, mock_llm):
    state: AgentState = {
        "user_query": "כמה טיסות נחתו אתמול?",
        "sql_query": "SELECT count(*) FROM flights WHERE status = 'Landed'",
        "sql_explanation": "שאילתה הסופרת את מספר הטיסות שנחתו",
        "inline_result_rows": [[42]],
        "inline_result_columns": ["flight_count"],
        "raw_data_ref": None,
        "runtime_flags": {"ESCA_WRITE_ENABLED": False},
        "summary": "",
        "execution_path": [],
        "messages": [],
        "query_enrichments": [],
        "jeen_catalog": "",
        "trino_error": None,
        "refinement_count": 0,
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "rejection_category": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "active_skills": None,
        "loaded_skills": None,
        "last_error": None,
        "esca_write_failed": False,
        "error_history": None,
        "schema_explorer_retry_count": 0,
        "scoping_mode": "hybrid",
    }

    mock_response = MagicMock()
    mock_response.content = "אתמול נחתו 42 טיסות בסך הכל."

    with patch("agent.nodes.finalizer.ChatPromptTemplate.from_messages") as mock_from_messages:
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_response)
        mock_from_messages.return_value.__or__.return_value = mock_chain

        res = await finalizer_node(state)

        assert res["summary"] == "אתמול נחתו 42 טיסות בסך הכל."
        assert res["sql_explanation"] == "שאילתה הסופרת את מספר הטיסות שנחתו"
        assert res["execution_path"] == ["finalizer"]

        # Check call arguments
        call_args = mock_chain.ainvoke.call_args[0][0]
        assert call_args["user_request"] == "כמה טיסות נחתו אתמול?"
        assert call_args["sql_query"] == "SELECT count(*) FROM flights WHERE status = 'Landed'"
        assert call_args["sql_translation"] == "שאילתה הסופרת את מספר הטיסות שנחתו"

        sql_results = json.loads(call_args["sql_results"])
        assert sql_results["columns"] == ["flight_count"]
        assert sql_results["preview_rows"] == [[42]]
        assert sql_results["total_rows"] == 1


@pytest.mark.asyncio
async def test_finalizer_node_with_top_10_preview(mock_langfuse, mock_llm):
    rows = [[i, f"Flight-{i}"] for i in range(25)]
    columns = ["id", "flight_code"]

    state: AgentState = {
        "user_query": "הצג טיסות",
        "sql_query": "SELECT id, flight_code FROM flights",
        "sql_explanation": "שליפת רשימת טיסות",
        "inline_result_rows": rows,
        "inline_result_columns": columns,
        "raw_data_ref": None,
        "runtime_flags": {"ESCA_WRITE_ENABLED": False},
        "summary": "",
        "execution_path": [],
        "messages": [],
        "query_enrichments": [],
        "jeen_catalog": "",
        "trino_error": None,
        "refinement_count": 0,
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "rejection_category": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "active_skills": None,
        "loaded_skills": None,
        "last_error": None,
        "esca_write_failed": False,
        "error_history": None,
        "schema_explorer_retry_count": 0,
        "scoping_mode": "hybrid",
    }

    mock_response = MagicMock()
    mock_response.content = "להלן סיכום הטיסות."

    with patch("agent.nodes.finalizer.ChatPromptTemplate.from_messages") as mock_from_messages:
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_response)
        mock_from_messages.return_value.__or__.return_value = mock_chain

        res = await finalizer_node(state)

        call_args = mock_chain.ainvoke.call_args[0][0]
        sql_results = json.loads(call_args["sql_results"])
        assert len(sql_results["preview_rows"]) == 10
        assert sql_results["preview_count"] == 10
        assert sql_results["total_rows"] == 25
