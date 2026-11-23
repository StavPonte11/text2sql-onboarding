from unittest.mock import AsyncMock, patch

import pytest

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


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
       return_value={"models": ["gpt-oss", "qwen"], "default": "gpt-oss"})
def test_models_endpoint_lists_what_the_agent_can_use(mock_call, client):
    response = client.get("/api/agent/models")
    assert response.status_code == 200, response.text
    assert mock_call.await_args.args == ("list_models", {})
    assert response.json() == {"models": ["gpt-oss", "qwen"], "default": "gpt-oss", "list_error": None}


@patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock,
       return_value={"models": [], "default": "gpt-oss", "list_error": "Could not list models: refused"})
def test_models_endpoint_keeps_the_default_when_listing_fails(mock_call, client):
    body = client.get("/api/agent/models").json()
    assert body["models"] == [] and body["default"] == "gpt-oss" and "refused" in body["list_error"]


@pytest.mark.parametrize("path, tool", [("/api/agent/chat", "chat_with_agent"), ("/api/agent/deep/chat", "deep_agent")])
def test_chosen_model_is_sent_and_omitted_otherwise(client, path, tool):
    with patch("app.routers.agent._call_agent_mcp", new_callable=AsyncMock, return_value=DEEP_RESPONSE) as mock_call:
        client.post(path, json={"query": "q", "connection_id": 89, "model": "qwen"})
        assert mock_call.await_args.args[0] == tool and mock_call.await_args.args[1]["model"] == "qwen"
        client.post(path, json={"query": "q", "connection_id": 89})
        assert "model" not in mock_call.await_args.args[1]


def test_agent_errors_inside_nested_task_groups_keep_their_status():
    from fastapi import HTTPException

    from app.routers.agent import _http_exception_in

    inner = HTTPException(status_code=400, detail="Model 'x' is not available")
    nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [inner])])
    assert _http_exception_in(nested) is inner
    assert _http_exception_in(BaseExceptionGroup("g", [RuntimeError("boom")])) is None
