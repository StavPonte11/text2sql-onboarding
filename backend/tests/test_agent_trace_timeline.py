"""The Agent Sandbox step details: a trace's observations from Langfuse."""

from unittest.mock import MagicMock, patch

import requests

from app.services.eval_question_trace import LangfuseSource

SVC = "app.services.eval_question_trace"
SOURCE = LangfuseSource("https://langfuse.example", "pk", "sk")

OBSERVATION = {
    "name": "execute_trino_query",
    "type": "TOOL",
    "startTime": "2026-11-25T08:00:00.000Z",
    "endTime": "2026-11-25T08:00:01.500Z",
    "inputUsage": 120,
    "outputUsage": 30,
    "model": "gpt-5",
    "statusMessage": "",
    "input": {"sql": "SELECT 1"},
    "output": {"rows": [[1]]},
}


def _response(status: int, body: dict | None = None) -> MagicMock:
    res = MagicMock(status_code=status, **{"json.return_value": body or {}})
    if status >= 400:
        res.raise_for_status.side_effect = requests.HTTPError(response=res)
    return res


def _langfuse(*responses):
    return (
        patch(f"{SVC}.langfuse_sources", return_value=[SOURCE]),
        patch(f"{SVC}.requests.get", side_effect=list(responses)),
    )


def test_steps_are_read_from_v2_observations_page_by_page(client):
    second = {**OBSERVATION, "name": "answer", "startTime": "2026-11-25T08:00:02.000Z"}
    first_page = [OBSERVATION] * 100
    sources, get = _langfuse(
        _response(200, {"data": first_page, "meta": {"cursor": "next"}}),
        _response(200, {"data": [second], "meta": {"cursor": None}}),
    )
    with sources, get as mock_get:
        response = client.get("/api/agent/traces/trace1")

    assert response.status_code == 200, response.text
    urls = [c.args[0] for c in mock_get.call_args_list]
    assert urls == ["https://langfuse.example/api/public/v2/observations"] * 2
    params = [c.kwargs["params"] for c in mock_get.call_args_list]
    assert params[0]["traceId"] == "trace1" and "cursor" not in params[0]
    assert params[1]["cursor"] == "next"

    steps = response.json()
    assert len(steps) == 101
    assert steps[0] == {
        "span_name": "execute_trino_query",
        "start_time": "2026-11-25T08:00:00.000Z",
        "duration_ms": 1500,
        "input_tokens": 120,
        "output_tokens": 30,
        "model": "gpt-5",
        "status": "success",
        "input_preview": "{'sql': 'SELECT 1'}",
        "output_preview": "{'rows': [[1]]}",
    }
    assert steps[-1]["span_name"] == "answer"


def test_no_steps_yet_is_an_empty_list(client):
    sources, get = _langfuse(_response(200, {"data": [], "meta": {}}))
    with sources, get:
        response = client.get("/api/agent/traces/trace1")
    assert response.status_code == 200
    assert response.json() == []


def test_self_hosted_langfuse_without_v2_uses_the_trace_api(client):
    legacy = {**OBSERVATION, "promptTokens": 7, "completionTokens": 3}
    del legacy["inputUsage"], legacy["outputUsage"]
    sources, get = _langfuse(
        _response(501), _response(200, {"observations": [legacy]})
    )
    with sources, get as mock_get:
        response = client.get("/api/agent/traces/trace1")

    assert mock_get.call_args_list[1].args[0].endswith("/api/public/traces/trace1")
    step = response.json()[0]
    assert (step["input_tokens"], step["output_tokens"]) == (7, 3)


def test_a_langfuse_error_is_passed_on(client):
    sources, get = _langfuse(_response(401, {"message": "bad key"}))
    with sources, get:
        response = client.get("/api/agent/traces/trace1")
    assert response.status_code == 401
    assert "Langfuse error" in response.json()["detail"]
