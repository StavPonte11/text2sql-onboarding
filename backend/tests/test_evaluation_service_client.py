"""Backend ↔ evaluation service: submit a background job and poll it."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.routers.evaluation import EvaluationCancelledError, _call_run_new_evaluation

REQ = {"dataset_name": "text2sql_connection_6", "connection_id": 6}


def _response(status_code=200, payload=None):
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = payload or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return resp


def _result_payload():
    return {
        "dataset_name": "text2sql_connection_6",
        "run_id": "r1",
        "total_cases": 1,
        "passed": 1,
        "failed": 0,
        "failure_rate": 0.0,
        "latency": dict(p50=1, p95=1, p99=1, average=1, minimum=1, maximum=1, total_samples=1),
        "accuracy": dict(
            execution_accuracy=1, contains_accuracy=1, sql_exact_match=0, time_shift_score=1,
            component_match=1, schema_hallucination=1, dialect_error=1, composite_score=1,
        ),
        "failure_analysis": dict(
            total_failures=0, failure_rate=0, agent_crash_count=0, agent_crash_rate=0,
            sql_execution_failure_count=0, sql_execution_failure_rate=0, trino_failure_count=0,
            trino_failure_rate=0, timeout_count=0, timeout_rate=0, validation_failure_count=0,
            validation_failure_rate=0, categories=[],
        ),
        "performance": dict(
            average_total_execution_time_ms=1, average_time_to_first_row_ms=1,
            total_token_usage=0, average_token_usage=0, average_refiner_iterations=0,
        ),
        "duration_seconds": 1.0,
        "cases": [{"question_id": "q1", "succeeded": True, "scores": {"contains_accuracy": 1.0}}],
    }


@pytest.fixture(autouse=True)
def fast_polling():
    with patch("app.routers.evaluation.settings.EVALUATION_POLL_INTERVAL_SECONDS", 0):
        yield


def test_polls_until_completed():
    polls = [
        _response(payload={"status": "running", "completed_items": 0, "total_items": 1}),
        _response(status_code=503),  # transient while the service is busy
        _response(payload={"status": "completed", "result": _result_payload()}),
    ]
    with patch("app.routers.evaluation.requests.post", return_value=_response(202, {"job_id": "j1"})), \
         patch("app.routers.evaluation.requests.get", side_effect=polls) as get:
        result = _call_run_new_evaluation(REQ)
    assert result.accuracy.contains_accuracy == 1
    assert get.call_count == 3
    assert get.call_args.args[0].endswith("/jobs/j1")


def test_failed_job_raises_with_its_error():
    with patch("app.routers.evaluation.requests.post", return_value=_response(202, {"job_id": "j1"})), \
         patch("app.routers.evaluation.requests.get",
               return_value=_response(payload={"status": "failed", "error": "Dataset not found"})):
        with pytest.raises(RuntimeError, match="Dataset not found"):
            _call_run_new_evaluation(REQ)


def test_cancellation_stops_polling():
    with patch("app.routers.evaluation.requests.post", return_value=_response(202, {"job_id": "j1"})), \
         patch("app.routers.evaluation.requests.get") as get:
        with pytest.raises(EvaluationCancelledError):
            _call_run_new_evaluation(REQ, is_cancelled=lambda: True)
    get.assert_not_called()


def test_lost_job_is_reported():
    with patch("app.routers.evaluation.requests.post", return_value=_response(202, {"job_id": "j1"})), \
         patch("app.routers.evaluation.requests.get", return_value=_response(404)):
        with pytest.raises(RuntimeError, match="restarted"):
            _call_run_new_evaluation(REQ)


def test_falls_back_to_blocking_endpoint_without_jobs_api():
    posts = [_response(404), _response(payload=_result_payload())]
    with patch("app.routers.evaluation.requests.post", side_effect=posts) as post:
        result = _call_run_new_evaluation(REQ)
    assert result.total_cases == 1
    assert post.call_args.args[0].endswith("/run-new-evaluation")


def test_requires_connection_id():
    with pytest.raises(ValueError):
        _call_run_new_evaluation({"dataset_name": "x"})
