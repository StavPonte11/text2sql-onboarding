"""
eval_question_trace.py — What Langfuse recorded for one question of an evaluation run.

The evaluation service writes one span per question, named eval-sample:<question id>,
with the SQL runs as child spans and the scores on the trace. Reads go through the
/v2/observations and /v3/scores endpoints, which Langfuse Cloud requires from
2026-11-16 on. Self-hosted Langfuse versions without them answer 501, and are read
through the older /observations and /traces/{id} endpoints instead.

The configured Langfuse (LANGFUSE_HOST) is read first. When it can't be read
or has no record of the question, Langfuse Cloud is tried as a fallback
(LANGFUSE_FALLBACK_HOST with its own LANGFUSE_FALLBACK_* keys), so runs
written there still show their details.

A Langfuse that doesn't answer in time isn't retried: the next source is tried
at once, and that host is skipped for _SKIP_DOWN_HOST_SECONDS so every other
question (a popup, an export page) doesn't wait on it again.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)

# (connect, read) seconds per request
_TIMEOUT_SECONDS = (5, 15)
# Attempts on a rate limit (429); a timeout or dropped connection isn't retried
_ATTEMPTS = 3
_SKIP_DOWN_HOST_SECONDS = 60
# host -> time.monotonic() until which it is skipped after not answering
_down_until: dict[str, float] = {}
# How a Langfuse without the v2/v3 read endpoints answers them
_NOT_AVAILABLE = {404, 405, 501}
_OBSERVATION_FIELDS = "core,basic,io,metadata"
# Added by the Langfuse SDK to every span; says nothing about the question
_SDK_METADATA = ("scope", "resourceAttributes")


@dataclass(frozen=True)
class LangfuseSource:
    host: str
    public_key: str
    secret_key: str


def langfuse_sources() -> list[LangfuseSource]:
    """The configured Langfuse, then Langfuse Cloud — each only when it has a host and keys."""
    candidates = [
        LangfuseSource(
            settings.LANGFUSE_HOST.rstrip("/"),
            settings.LANGFUSE_PUBLIC_KEY,
            settings.LANGFUSE_SECRET_KEY,
        ),
        LangfuseSource(
            settings.LANGFUSE_FALLBACK_HOST.rstrip("/"),
            settings.LANGFUSE_FALLBACK_PUBLIC_KEY,
            settings.LANGFUSE_FALLBACK_SECRET_KEY,
        ),
    ]
    sources: list[LangfuseSource] = []
    for source in candidates:
        if (
            source.host
            and source.public_key
            and source.secret_key
            and source.host not in {s.host for s in sources}
        ):
            sources.append(source)
    return sources


def _request(source: LangfuseSource, path: str, **params: Any) -> dict:
    """GET with retries on rate limits (429).

    A timeout or dropped connection marks the host down for a while and is
    raised at once, so the caller moves on to the next source.
    """
    if _down_until.get(source.host, 0.0) > time.monotonic():
        raise requests.ConnectionError(
            f"{source.host} didn't answer recently; skipped for now"
        )
    for attempt in range(_ATTEMPTS):
        try:
            res = requests.get(
                f"{source.host}/api/public/{path}",
                auth=(source.public_key, source.secret_key),
                params=params,
                timeout=_TIMEOUT_SECONDS,
            )
        except (requests.ConnectionError, requests.Timeout):
            _down_until[source.host] = time.monotonic() + _SKIP_DOWN_HOST_SECONDS
            raise
        if res.status_code != 429 or attempt == _ATTEMPTS - 1:
            break
        time.sleep(2**attempt)
    _down_until.pop(source.host, None)
    res.raise_for_status()
    return res.json()


def _get(source: LangfuseSource, path: str, **params: Any) -> list[dict]:
    return _request(source, path, **params).get("data", [])


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


def _latest(samples: list[dict]) -> dict | None:
    return max(samples, key=lambda s: s.get("startTime") or "") if samples else None


def _read_current(
    source: LangfuseSource, question_id: str, window: dict
) -> tuple | None:
    sample = _latest(
        _get(
            source,
            "v2/observations",
            name=f"eval-sample:{question_id}",
            fields=_OBSERVATION_FIELDS,
            **window,
        )
    )
    if sample is None:
        return None
    trace_id = sample["traceId"]
    observations = _get(
        source,
        "v2/observations",
        traceId=trace_id,
        fields=_OBSERVATION_FIELDS,
        **window,
    )
    scores = _get(
        source,
        "v3/scores",
        traceId=trace_id,
        fromTimestamp=window["fromStartTime"],
        toTimestamp=window["toStartTime"],
        limit=100,
    )
    return sample, observations, scores


def _read_legacy(
    source: LangfuseSource, question_id: str, window: dict
) -> tuple | None:
    sample = _latest(
        _get(source, "observations", name=f"eval-sample:{question_id}", **window)
    )
    if sample is None:
        return None
    trace = _request(source, f"traces/{sample['traceId']}")
    return sample, trace.get("observations") or [], trace.get("scores") or []


def fetch_question_trace(
    question_id: str, started_at: datetime, ended_at: datetime
) -> dict | None:
    """
    Everything Langfuse holds for a question evaluated between the two times
    (UTC), or None when every Langfuse was read and none has a record of it.
    Tries each of langfuse_sources() in turn; when one couldn't be read and no
    other has the record, raises that error (the record may be in the one that
    failed, so "no record" would hide a setup problem).
    """
    window = {
        "fromStartTime": f"{started_at.isoformat()}Z",
        "toStartTime": f"{ended_at.isoformat()}Z",
    }
    error: requests.RequestException | None = None
    for source in langfuse_sources():
        try:
            found = _read_from(source, question_id, window)
        except requests.RequestException as exc:
            logger.warning(
                f"[Eval] Langfuse at {source.host} failed for {question_id}: {exc}"
            )
            error = exc
            continue
        if found is not None:
            return _question_record(source, *found)
    if error is not None:
        raise error
    return None


def _read_from(source: LangfuseSource, question_id: str, window: dict) -> tuple | None:
    try:
        return _read_current(source, question_id, window)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code not in _NOT_AVAILABLE:
            raise
        return _read_legacy(source, question_id, window)


def _question_record(
    source: LangfuseSource, sample: dict, observations: list[dict], scores: list[dict]
) -> dict:
    trace_id = sample["traceId"]
    spans = {
        o["name"]: o
        for o in observations
        if o.get("parentObservationId") == sample["id"]
    }

    output = _as_dict(sample.get("output"))
    agent = spans.get("agent_execution") or {}
    return {
        "trace_id": trace_id,
        "langfuse_url": f"{source.host}/project/{sample.get('projectId')}/traces/{trace_id}",
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
