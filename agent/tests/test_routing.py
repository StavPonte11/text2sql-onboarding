import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from agent.state import AgentState
from agent.graph import (
    validate_config_node,
    InvalidConfigurationException,
    rejection_router_node,
    route_refiner_subagent,
)
from agent.nodes.refiner import trino_exec_node
from agent.config import settings


@pytest.mark.asyncio
async def test_tts_g1_04_error_and_feedback_loop_routing(mock_langfuse, mock_llm):
    # 1. Verify rejection_router
    # If feedback_route is 'extractor', it should clear sql_query, schema_plan, raw_data_ref, etc.
    state: AgentState = {
        "user_query": "test query",
        "feedback": "I don't like the plan",
        "sql_query": "SELECT * FROM t",
        "schema_plan": "Use table t",
        "raw_data_ref": "esca_123",
        "trino_error": "Syntax error",
        "messages": [],
        "query_enrichments": [],
        "refinement_count": 0,
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "last_error": None,
        "hallucinated_tables": None,
        "esca_write_failed": None,
        "inline_result_rows": None,
        "attempt_history": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
    }

    result = rejection_router_node(state)
    assert result["feedback_route"] == "extractor"
    assert result["raw_data_ref"] is None
    assert result["trino_error"] is None


@pytest.mark.asyncio
async def test_tts_g1_08_refiner_context_accumulation(
    mock_langfuse, mock_llm, mock_trino
):
    state: AgentState = {
        "user_query": "test query",
        "sql_query": "SELECT bad",
        "schema_plan": "plan",
        "trino_error": None,
        "attempt_history": [{"iteration": 1, "sql": "SELECT bad1", "success": False, "error": "Error 1", "row_count": None}, {"iteration": 2, "sql": "SELECT bad2", "success": False, "error": "Error 2", "row_count": None}],  # Accumulated previous attempts
        "refinement_count": 2,
        "messages": [],
        "query_enrichments": [],
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "last_error": None,
        "hallucinated_tables": None,
        "esca_write_failed": None,
        "inline_result_rows": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
    }

    # Mock execute_query_sync to fail to add a new error
    class FakeErrorResult:
        success = False
        error_message = "Error 3"
        rows = []
        columns = []

    with patch(
        "agent.nodes.refiner.execute_query_sync", return_value=FakeErrorResult()
    ):
        with patch("agent.nodes.refiner.get_esca_client"):
            result = await trino_exec_node(state)

            # Verify attempt history accumulation
            assert "attempt_history" in result
            assert len(result["attempt_history"]) == 3
            # First two entries are the original history, third is the new failure
            assert result["attempt_history"][0] == {"iteration": 1, "sql": "SELECT bad1", "success": False, "error": "Error 1", "row_count": None}
            assert result["attempt_history"][1] == {"iteration": 2, "sql": "SELECT bad2", "success": False, "error": "Error 2", "row_count": None}
            assert result["attempt_history"][2]["sql"] == "SELECT bad"
            assert result["attempt_history"][2]["error"] == "Error 3"
            assert result["attempt_history"][2]["success"] is False


def test_tts_g2_01_scoping_modes_strict_vs_hybrid():
    # Strict mode with None allowed tables
    state_strict_fail: AgentState = {
        "scoping_mode": "strict",
        "allowed_tables": None,
        "user_query": "",
        "messages": [],
        "query_enrichments": [],
        "schema_plan": "",
        "sql_query": "",
        "trino_error": None,
        "refinement_count": 0,
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
        "allowed_statuses": None,
        "feedback": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "last_error": None,
        "hallucinated_tables": None,
        "esca_write_failed": None,
        "inline_result_rows": None,
        "attempt_history": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
    }
    with pytest.raises(InvalidConfigurationException):
        validate_config_node(state_strict_fail)

    # Strict mode with allowed tables
    state_strict_pass = dict(state_strict_fail)
    state_strict_pass["allowed_tables"] = ["t1"]
    res = validate_config_node(state_strict_pass)
    assert res["scoping_mode"] == "strict"


def test_tts_g2_02_max_loop_and_hitl_breakpointer():
    state: AgentState = {
        "refinement_count": settings.MAX_REFINER_ITERATIONS,
        "trino_error": "still failing",
        "user_query": "",
        "messages": [],
        "query_enrichments": [],
        "schema_plan": "",
        "sql_query": "",
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "feedback_route": None,
        "non_interactive": False,
        "active_extractors": None,
        "last_error": None,
        "hallucinated_tables": None,
        "esca_write_failed": None,
        "inline_result_rows": None,
        "attempt_history": None,
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
    }

    # route_refiner_subagent should return hitl_escalation when trino_error is set after hitting subgraph limit
    route = route_refiner_subagent(state)
    assert route == "hitl_escalation"

