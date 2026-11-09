"""
eval_question_trace.py — What Langfuse recorded for one question of an evaluation run.

The evaluation service writes one span per question, named eval-sample:<question id>,
with the SQL runs as child spans and the scores on the trace. Reads go through the
/v2/observations and /v3/scores endpoints: the older /traces and /datasets/.../runs
ones are retired from Langfuse Cloud on 2026-11-16.
"""

import json
import time
from datetime import datetime
from typing import Any

import requests

from app.config import settings

_TIMEOUT_SECONDS = 30
_RATE_LIMIT_ATTEMPTS = 3
_OBSERVATION_FIELDS = "core,basic,io,metadata"
# Added by the Langfuse SDK to every span; says nothing about the question
_SDK_METADATA = ("scope", "resourceAttributes")


def _get(path: str, **params: Any) -> list[dict]:
    for attempt in range(_RATE_LIMIT_ATTEMPTS):
        res = requests.get(
            f"{settings.LANGFUSE_HOST}/api/public/{path}",
            auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
            params=params,
            timeout=_TIMEOUT_SECONDS,
        )
        if res.status_code != 429:
            break
        if attempt < _RATE_LIMIT_ATTEMPTS - 1:
            time.sleep(2**attempt)
    res.raise_for_status()
    return res.json().get("data", [])


def _parsed(value: Any) -> Any:
    """Langfuse returns a span's input and output as JSON strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _as_dict(value: Any) -> dict:
    parsed = _parsed(value)
    return parsed if isinstance(parsed, dict) else {}


def _sql_run(span: dict | None) -> dict | None:
    if span is None:
        return None
    return {
        "sql": _as_dict(span.get("input")).get("sql"),
        "execution_time_ms": (span.get("metadata") or {}).get("execution_time_ms"),
        **_as_dict(span.get("output")),
    }


def _merged_scores(evaluations: dict, langfuse_scores: list[dict]) -> list[dict]:
    """One row per score: evaluators (even not-applicable ones), then anything else."""
    values = {s["name"]: s.get("value") for s in langfuse_scores}
    rows = [
        {
            "name": name,
            "value": values.get(name, evaluation.get("score")),
            "passed": evaluation.get("passed"),
            "applicable": evaluation.get("applicable", True),
            "error": evaluation.get("error"),
            "details": evaluation.get("details") or {},
        }
        for name, evaluation in evaluations.items()
    ]
    rows += [
        {
            "name": name,
            "value": value,
            "passed": None,
            "applicable": True,
            "error": None,
            "details": {},
        }
        for name, value in values.items()
        if name not in evaluations
    ]
    return rows


def fetch_question_trace(
    question_id: str, started_at: datetime, ended_at: datetime
) -> dict | None:
    """
    Everything Langfuse holds for a question evaluated between the two times
    (UTC), or None when it has no record of it. Raises requests exceptions when
    Langfuse can't be read.
    """
    window = {
        "fromStartTime": f"{started_at.isoformat()}Z",
        "toStartTime": f"{ended_at.isoformat()}Z",
    }
    samples = _get(
        "v2/observations",
        name=f"eval-sample:{question_id}",
        fields=_OBSERVATION_FIELDS,
        **window,
    )
    if not samples:
        return None
    sample = max(samples, key=lambda s: s.get("startTime") or "")
    trace_id = sample["traceId"]

    observations = _get(
        "v2/observations", traceId=trace_id, fields=_OBSERVATION_FIELDS, **window
    )
    spans = {
        o["name"]: o
        for o in observations
        if o.get("parentObservationId") == sample["id"]
    }
    scores = _get(
        "v3/scores",
        traceId=trace_id,
        fromTimestamp=window["fromStartTime"],
        toTimestamp=window["toStartTime"],
        limit=100,
    )

    output = _as_dict(sample.get("output"))
    agent = spans.get("agent_execution") or {}
    return {
        "trace_id": trace_id,
        "langfuse_url": f"{settings.LANGFUSE_HOST}/project/{sample.get('projectId')}/traces/{trace_id}",
        "succeeded": output.get("succeeded"),
        "error": output.get("error"),
        "generated_sql": output.get("generated_sql"),
        "executed_generated_sql": output.get("executed_generated_sql"),
        "expected_sql": output.get("expected_sql"),
        "agent_status": _as_dict(agent.get("output")).get("status"),
        "expected_run": _sql_run(spans.get("expected_sql_execution")),
        "generated_run": _sql_run(spans.get("generated_sql_execution")),
        "scores": _merged_scores(output.get("evaluations") or {}, scores),
        "metadata": {
            key: value
            for key, value in (sample.get("metadata") or {}).items()
            if key not in _SDK_METADATA
        },
    }
