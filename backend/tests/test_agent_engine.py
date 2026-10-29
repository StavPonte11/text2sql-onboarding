from unittest.mock import AsyncMock, patch

DEEP_RESPONSE = {
    "thread_id": "deep-123", "status": "completed", "summary": "466 הזמנות", "sql_query": "SELECT 1",
    "sql_explanation": "x", "trace_id": "t", "execution_path": ["deep:execute_trino_query"],
    "is_unanswerable": False, "engine": "deep", "verification": "matched", "confidence": "high",
    "executed": True, "recovered_from_text": False,
}


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_chat_forwards_engine_and_returns_verification(mock_call, client):
    response = client.post("/api/agent/chat", json={"query": "כמה הזמנות", "connection_id": 89, "engine": "deep"})
    assert response.status_code == 200, response.text
    assert mock_call.await_args.args[0]["engine"] == "deep"
    body = response.json()
    assert body["engine"] == "deep" and body["verification"] == "matched" and body["confidence"] == "high"
    assert body["executed"] is True and body["recovered_from_text"] is False


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE)
def test_chat_tells_the_agent_how_long_it_waits(mock_call, client):
    from app.routers.agent import AGENT_STOP_MARGIN_S, CHAT_TIMEOUT_S

    client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "engine": "deep"})
    assert mock_call.await_args.args[0]["timeout_s"] == CHAT_TIMEOUT_S - AGENT_STOP_MARGIN_S < CHAT_TIMEOUT_S


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
       return_value={"thread_id": "t", "status": "completed", "summary": "ok"})
def test_chat_omits_engine_when_not_chosen(mock_call, client):
    response = client.post("/api/agent/chat", json={"query": "q", "connection_id": 89})
    assert response.status_code == 200, response.text
    assert "engine" not in mock_call.await_args.args[0]
    assert response.json()["engine"] is None


def test_chat_rejects_unknown_engine(client):
    response = client.post("/api/agent/chat", json={"query": "q", "connection_id": 89, "engine": "gpt"})
    assert response.status_code == 422
