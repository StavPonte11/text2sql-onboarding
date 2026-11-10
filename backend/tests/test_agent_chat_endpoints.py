from unittest.mock import AsyncMock, patch

DEEP_RESPONSE = {
    "thread_id": "t-123", "status": "completed", "summary": "466 הזמנות", "sql_query": "SELECT 1",
    "sql_explanation": "x", "trace_id": "t", "execution_path": ["deep:execute_trino_query"],
    "is_unanswerable": False, "verification": "matched", "confidence": "high",
    "executed": True, "recovered_from_text": False,
}


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_deep_endpoint_calls_the_deep_agent_tool(mock_call, client):
    response = client.post("/api/agent/deep/chat", json={"query": "כמה הזמנות", "connection_id": 89})
    assert response.status_code == 200, response.text
    tool, args = mock_call.await_args.args
    assert tool == "deep_agent"
    assert set(args) == {"query", "thread_id", "resume_value", "hitl_enabled", "connection_id", "timeout_s"}
    body = response.json()
    assert body["verification"] == "matched" and body["confidence"] == "high"
    assert body["executed"] is True and body["recovered_from_text"] is False


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_deep_endpoint_tells_the_agent_how_long_it_waits(mock_call, client):
    from app.config import settings
    from app.routers.agent import AGENT_STOP_MARGIN_S

    client.post("/api/agent/deep/chat", json={"query": "q", "connection_id": 89})
    wait = settings.AGENT_READ_TIMEOUT_SECONDS
    assert mock_call.await_args.args[1]["timeout_s"] == wait - AGENT_STOP_MARGIN_S < wait


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_deep_endpoint_continues_a_thread(mock_call, client):
    response = client.post("/api/agent/deep/chat",
                           json={"thread_id": "t-123", "resume_value": "חיפה", "connection_id": 89})
    assert response.status_code == 200, response.text
    tool, args = mock_call.await_args.args
    assert tool == "deep_agent" and args["thread_id"] == "t-123" and args["resume_value"] == "חיפה"


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
       return_value={"thread_id": "t", "status": "completed", "summary": "ok"})
def test_chat_endpoint_calls_the_legacy_tool(mock_call, client):
    response = client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "allowed_tables": ["t"]})
    assert response.status_code == 200, response.text
    tool, args = mock_call.await_args.args
    assert tool == "chat_with_agent" and args["allowed_tables"] == ["t"]
    assert "timeout_s" not in args


def test_chat_endpoint_no_longer_accepts_an_engine_choice(client):
    with patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
               return_value={"thread_id": "t", "status": "completed"}) as mock_call:
        client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "engine": "deep"})
    assert mock_call.await_args.args[0] == "chat_with_agent"  # the endpoint decides, not a field
