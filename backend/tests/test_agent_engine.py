from unittest.mock import AsyncMock, patch

DEEP_RESPONSE = {
    "thread_id": "deep-123", "status": "completed", "summary": "466 הזמנות", "sql_query": "SELECT 1",
    "sql_explanation": "x", "trace_id": "t", "execution_path": ["deep:execute_trino_query"],
    "is_unanswerable": False, "engine": "deep", "verification": "matched", "confidence": "high",
    "executed": True, "recovered_from_text": False,
}


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_deep_engine_calls_the_deep_agent_tool(mock_call, client):
    response = client.post("/api/agent/chat", json={"query": "כמה הזמנות", "connection_id": 89, "engine": "deep",
                                                     "allowed_tables": ["t"]})
    assert response.status_code == 200, response.text
    tool, args = mock_call.await_args.args
    assert tool == "deep_agent" and "engine" not in args
    assert "allowed_tables" not in args  # legacy-only arguments are not sent to the deep agent
    body = response.json()
    assert body["engine"] == "deep" and body["verification"] == "matched" and body["confidence"] == "high"
    assert body["executed"] is True and body["recovered_from_text"] is False


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_chat_tells_the_agent_how_long_it_waits(mock_call, client):
    from app.config import settings
    from app.routers.agent import AGENT_STOP_MARGIN_S

    client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "engine": "deep"})
    wait = settings.AGENT_READ_TIMEOUT_SECONDS
    assert mock_call.await_args.args[1]["timeout_s"] == wait - AGENT_STOP_MARGIN_S < wait


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
       return_value={"thread_id": "t", "status": "completed", "summary": "ok"})
def test_default_calls_the_legacy_tool(mock_call, client):
    response = client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "allowed_tables": ["t"]})
    assert response.status_code == 200, response.text
    tool, args = mock_call.await_args.args
    assert tool == "chat_with_agent" and args["allowed_tables"] == ["t"]
    assert "engine" not in args and "timeout_s" not in args
    assert response.json()["engine"] is None


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_resuming_a_deep_thread_stays_on_the_deep_agent(mock_call, client):
    response = client.post("/api/agent/chat", json={"thread_id": "deep-123", "resume_value": "חיפה", "connection_id": 89})
    assert response.status_code == 200, response.text
    assert mock_call.await_args.args[0] == "deep_agent"


def test_chat_rejects_unknown_engine(client):
    response = client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "engine": "gpt"})
    assert response.status_code == 422
