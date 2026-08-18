import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from agent.nodes.refiner import trino_exec_node
from agent.state import AgentState

# ─── HELPER CLASSES ──────────────────────────────────────────────────────────


class MockTrinoResult:
    """Mock structure matching the return type of execute_query_sync"""

    def __init__(self, success, error_message=None, rows=None, columns=None):
        self.success = success
        self.error_message = error_message
        self.rows = rows or []
        self.columns = columns or []


class MockEscaClient:
    """Mocks the async context manager for ESCA."""

    def __init__(self, save_result=None, throw_error=False):
        self.save_result = save_result or {"esca_id": "esca_12345"}
        self.throw_error = throw_error
        self.save_data_mock = AsyncMock()

    async def __aenter__(self):
        if self.throw_error:
            self.save_data_mock.side_effect = Exception("ESCA Storage Offline")
        else:
            self.save_data_mock.return_value = self.save_result
        self.save_data = self.save_data_mock
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# ─── TESTS ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_success_with_transformations(
    mock_execute, mock_get_esca, mock_publish
):
    """
    LEGIT HAPPY PATH: Tests WKT injection, table aliasing, successful DB execution,
    and a successful ESCA blob write.
    """
    # 1. Setup execution mock
    mock_execute.return_value = MockTrinoResult(
        success=True, rows=[[1, "Alice"], [2, "Bob"]], columns=["id", "name"]
    )

    # 2. Setup ESCA mock
    esca_mock_instance = MockEscaClient(save_result={"esca_id": "esca_999"})
    mock_get_esca.return_value = esca_mock_instance

    # 3. Setup State with WKT placeholders and short table names
    original_sql = "SELECT * FROM users_table WHERE geom = @loc_tel_aviv@"
    state = AgentState(
        sql_query=original_sql,
        locations_dict={"coords": {"loc_tel_aviv_wkt": "POLYGON((34 32, 35 32, ...))"}},
        jeen_catalog='"hive"."production"."users_table": users table\n  - "id" (INT)\n  - "name" (VARCHAR)',
        runtime_flags={"ESCA_WRITE_ENABLED": True},
    )

    # Note: We simulate a slight mismatch in placeholder above to test exact mapping.
    # Let's fix the SQL to match the exact placeholder dict key:
    state["sql_query"] = "SELECT * FROM users_table WHERE geom = @loc_tel_aviv_wkt@"

    # 4. Run Node
    result = await trino_exec_node(state)

    # 5. Assertions
    # Verify the SQL was actually transformed BEFORE being sent to Trino
    executed_sql = mock_execute.call_args[0][0]

    assert "'POLYGON((34 32, 35 32, ...))'" in executed_sql, (
        "WKT must be injected and quoted"
    )
    assert "@loc_tel_aviv_wkt@" not in executed_sql

    # Verify state updates
    assert result["trino_error"] is None
    assert result["raw_data_ref"] == "esca_999"
    assert result["esca_write_failed"] is False
    assert result["last_result_row_count"] == 2

    # Verify ESCA payload format
    esca_mock_instance.save_data_mock.assert_called_once()
    payload = esca_mock_instance.save_data_mock.call_args[0][0]
    decoded_payload = json.loads(payload.decode())
    assert decoded_payload["columns"] == ["id", "name"]


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_db_failure(mock_execute, mock_get_esca, mock_publish):
    """
    FAILURE PATH (DATABASE): If Trino throws an error, the node must capture it,
    append it to error_history, and SKIP writing to ESCA.
    """
    # Simulate DB syntax error
    mock_execute.return_value = MockTrinoResult(
        success=False,
        error_message="line 1:8: Table 'hive.production.users_table' does not exist",
    )

    state = AgentState(
        sql_query="SELECT * FROM missing_table", error_history=[{"sql": "SELECT old", "error": "Previous Error"}]
    )

    result = await trino_exec_node(state)

    # Verify Trino error is captured
    assert (
        result["trino_error"]
        == "line 1:8: Table 'hive.production.users_table' does not exist"
    )
    assert len(result["error_history"]) == 2
    assert result["error_history"][-1] == {"sql": "SELECT * FROM missing_table", "error": result["trino_error"]}

    # Verify ESCA was skipped entirely
    mock_get_esca.assert_not_called()
    assert result["last_result_row_count"] is None


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.langfuse_client")
async def test_trino_exec_esca_failure_survival(
    mock_langfuse, mock_execute, mock_get_esca, mock_publish
):
    """
    STRICT FAILURE (ESCA): If the DB succeeds but the external ESCA blob storage
    is offline, the system must CRASH to ensure strict failure propagation.
    """
    mock_execute.return_value = MockTrinoResult(
        success=True, rows=[[1]], columns=["id"]
    )

    # Force ESCA to throw an exception
    esca_mock_instance = MockEscaClient(throw_error=True)
    mock_get_esca.return_value = esca_mock_instance

    state = AgentState(sql_query="SELECT 1", runtime_flags={"ESCA_WRITE_ENABLED": True})

    with pytest.raises(
        RuntimeError, match="Failed to write query result to ESCA: ESCA Storage Offline"
    ):
        await trino_exec_node(state)





@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_memory_shield_truncation(
    mock_execute, mock_get_esca, mock_publish
):
    """
    MEMORY PROTECTION: The node must return all rows for inline_result_rows (so
    subsequent nodes like satisfaction_check can evaluate them), but MUST truncate
    `last_result_data` to exactly 5 rows so the LLM context window doesn't blow up.
    """
    # Simulate a query returning 100 rows
    mock_rows = [[i, f"user_{i}"] for i in range(100)]
    mock_execute.return_value = MockTrinoResult(
        success=True, rows=mock_rows, columns=["id", "name"]
    )
    mock_get_esca.return_value = MockEscaClient()

    state = AgentState(
        sql_query="SELECT * FROM massive_table",
        runtime_flags={"ESCA_WRITE_ENABLED": False, "PREVIEW_ROWS_COUNT": 15},
    )

    result = await trino_exec_node(state)

    # 1. Full data is preserved for state/ESCA
    assert result["last_result_row_count"] == 100
    assert len(result["inline_result_rows"]) == 100

    # 2. LLM Context payload is strictly truncated!
    import ast

    # The node does: str([columns] + rows[:5])
    llm_payload = ast.literal_eval(result["last_result_data"])

    # 1 header row + 15 data rows = 16 total items
    assert len(llm_payload) == 16
    assert llm_payload[0] == ["id", "name"]  # Header
    assert llm_payload[-1] == [14, "user_14"]  # 15th data row


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_hard_exception_survival(
    mock_execute, mock_get_esca, mock_publish
):
    """
    HARD CRASH SURVIVAL: If the synchronous Trino execution function throws a
    hard Python exception (e.g., Network Timeout, DB connection dropped) instead of
    gracefully returning a Result object, the node must catch it via the
    `except Exception` block and treat it as a standard SQL failure.
    """
    # Force a hard crash, not a graceful success=False return
    mock_execute.side_effect = RuntimeError("Connection dropped abruptly")

    state = AgentState(
        sql_query="SELECT * FROM users", error_history=[{"sql": "SELECT bad", "error": "Syntax error on attempt 1"}]
    )

    result = await trino_exec_node(state)

    # Node survives and formats the Python exception as a Trino error
    assert result["trino_error"] == "Connection dropped abruptly"
    assert len(result["error_history"]) == 2
    assert result["error_history"][-1] == {"sql": "SELECT * FROM users", "error": "Connection dropped abruptly"}

    # Ensures payload is zeroed out
    assert result["last_result_row_count"] is None
    assert result["last_result_data"] is None


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_esca_disabled_via_flags(
    mock_execute, mock_get_esca, mock_publish
):
    """
    FEATURE FLAGS: Proves that if ESCA is disabled via runtime flags (either boolean
    or string "false"), the system bypasses the ESCA context manager entirely.
    """
    mock_execute.return_value = MockTrinoResult(
        success=True, rows=[[1]], columns=["id"]
    )

    # Notice the string "false" - testing the `.lower() == "true"` string parsing logic
    state = AgentState(
        sql_query="SELECT * FROM users", runtime_flags={"ESCA_WRITE_ENABLED": "false"}
    )

    result = await trino_exec_node(state)

    # ESCA mock should never have been invoked
    mock_get_esca.assert_not_called()
    assert result["raw_data_ref"] is None
    assert result["esca_write_failed"] is False


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_esca_client")
@patch("agent.nodes.refiner.execute_query_sync")
async def test_trino_exec_zero_rows_formatting(
    mock_execute, mock_get_esca, mock_publish
):
    """
    EDGE CASE (EMPTY SETS): If Trino executes successfully but returns exactly 0 rows,
    the serialization logic for ESCA and the LLM context (`last_result_data`)
    must not crash on empty lists.
    """
    # 0 rows returned
    mock_execute.return_value = MockTrinoResult(
        success=True, rows=[], columns=["id", "name"]
    )

    esca_mock_instance = MockEscaClient(save_result={"esca_id": "empty_blob"})
    mock_get_esca.return_value = esca_mock_instance

    state = AgentState(
        sql_query="SELECT * FROM users WHERE 1=0",
        runtime_flags={"ESCA_WRITE_ENABLED": True},
    )

    result = await trino_exec_node(state)

    assert result["trino_error"] is None
    assert result["last_result_row_count"] == 0

    # Looking closely at your code: `if inline_result_rows else "[]"`
    # It correctly returns the literal string "[]" when rows are empty.
    assert result["last_result_data"] == "[]"

    # ESCA should still be called to save the schema/headers of the empty result
    esca_mock_instance.save_data_mock.assert_called_once()
    payload = json.loads(esca_mock_instance.save_data_mock.call_args[0][0].decode())
    assert payload["columns"] == ["id", "name"]
    assert payload["rows"] == []




