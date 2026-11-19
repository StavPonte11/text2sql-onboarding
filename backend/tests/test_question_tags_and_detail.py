"""Running a connection by question tag, and the per-question detail."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.models.models import EvalResult, EvalRun, EvalStatus, GoldenQuestion

from app.services.eval_question_trace import fetch_question_trace
from app.services.jeen_metadata import fetch_jeen_sql_pairs_with_tags

_next_connection = iter(range(4300, 4400))
ORCH = "app.routers.orchestration"


def _pairs(*tagged):
    return [
        {"question": f"q{i}?", "sql_statement": f"SELECT {i}", "tags": tags}
        for i, tags in enumerate(tagged)
    ]


def _run_connection(client, connection_id, pairs, **params):
    with (
        patch(f"{ORCH}.fetch_jeen_sql_pairs_with_tags", AsyncMock(return_value=pairs)),
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}._run_dataset_pipeline") as pipeline,
    ):
        langfuse.enabled = False
        response = client.post(
            "/api/evaluations/run-connection",
            params={"connection_id": connection_id, **params},
        )
    return response, pipeline


def test_run_connection_with_tags_selects_any_matching_question(client, db_session):
    connection_id = next(_next_connection)
    response, pipeline = _run_connection(
        client,
        connection_id,
        _pairs(["validation"], ["test"], ["other"], ["test", "validation"]),
        tags=["validation", "test"],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["total_questions"] == 3
    assert body["question_tags"] == ["validation", "test"]

    run = db_session.get(EvalRun, body["id"])
    selected = {
        db_session.get(GoldenQuestion, qid).question for qid in run.question_ids
    }
    assert selected == {"q0?", "q1?", "q3?"}
    assert pipeline.call_args.args[3] == run.question_ids


def test_run_connection_without_tags_runs_all(client):
    response, pipeline = _run_connection(
        client, next(_next_connection), _pairs(["a"], ["b"], [])
    )
    assert response.status_code == 202
    assert response.json()["total_questions"] == 3
    assert response.json()["question_tags"] is None
    assert pipeline.call_args.args[3] is None


def test_run_connection_with_unknown_tag_is_rejected(client):
    response, pipeline = _run_connection(
        client, next(_next_connection), _pairs(["a"]), tags=["missing"]
    )
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]
    pipeline.assert_not_called()


def test_report_lists_only_the_runs_questions_as_pending(client, db_session):
    connection_id = next(_next_connection)
    response, _ = _run_connection(
        client, connection_id, _pairs(["keep"], ["other"]), tags=["keep"]
    )
    report = client.get(f"/api/evaluations/runs/{response.json()['id']}/report").json()
    assert [q["question"] for q in report["per_question"]] == ["q0?"]


@pytest.mark.asyncio
async def test_tags_are_normalized_and_filled_from_knowledge_questions():
    pairs = [
        {"question": "a?", "tags": [{"name": "x"}, " y ", "y"]},
        {"question": "b?", "tags": "m, n"},
        {"question": "c?"},
        {"question": "d?"},
    ]
    knowledge = [{"question": "c?", "tags": ["from-knowledge"]}]
    with (
        patch(
            "app.services.jeen_metadata.fetch_jeen_sql_pairs",
            AsyncMock(return_value=pairs),
        ),
        patch(
            "app.services.jeen_metadata.fetch_jeen_knowledge_questions",
            AsyncMock(return_value=knowledge),
        ),
    ):
        result = await fetch_jeen_sql_pairs_with_tags(1)
    assert [p["tags"] for p in result] == [
        ["x", "y"],
        ["m", "n"],
        ["from-knowledge"],
        [],
    ]


@pytest.mark.asyncio
async def test_pairs_survive_a_knowledge_questions_failure():
    with (
        patch(
            "app.services.jeen_metadata.fetch_jeen_sql_pairs",
            AsyncMock(return_value=[{"question": "a?"}]),
        ),
        patch(
            "app.services.jeen_metadata.fetch_jeen_knowledge_questions",
            AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        result = await fetch_jeen_sql_pairs_with_tags(1)
    assert result == [{"question": "a?", "tags": []}]


SAMPLE = {
    "id": "sample",
    "traceId": "trace1",
    "projectId": "proj",
    "startTime": "2026-10-26T12:50:51Z",
    "name": "eval-sample:q1",
    "metadata": {
        "total_latency_ms": 100,
        "scope": {"name": "sdk"},
        "resourceAttributes": {},
    },
    "output": (
        '{"generated_sql": "SELECT 2", "expected_sql": "SELECT 1", "succeeded": false,'
        ' "error": "boom", "evaluations": {'
        '"contains_accuracy": {"score": 0, "passed": false, "applicable": true,'
        ' "details": {"reason": "no_match"}},'
        '"time_shift": {"score": 0, "passed": false, "applicable": false}}}'
    ),
}
CHILDREN = [
    SAMPLE,
    {
        "name": "expected_sql_execution",
        "parentObservationId": "sample",
        "input": '{"sql": "SELECT 1"}',
        "output": '{"success": true, "columns": ["a"], "rows": [[1]], "row_count": 1}',
        "metadata": {"execution_time_ms": 12.5},
    },
    {
        "name": "expected_sql_execution",
        "parentObservationId": None,
        "output": '{"wrapper": 1}',
    },
    {
        "name": "agent_execution",
        "parentObservationId": "sample",
        "output": '{"status": "error"}',
    },
]
SCORES = [
    {"name": "contains_accuracy", "value": 0},
    {"name": "agent_sql_generation_seconds", "value": 283.83},
]


def _langfuse(*pages):
    responses = [MagicMock(**{"json.return_value": {"data": page}}) for page in pages]
    return patch("app.services.eval_question_trace.requests.get", side_effect=responses)


def test_fetch_question_trace_collects_runs_scores_and_metadata():
    start = datetime(2026, 10, 26, 12, 0)
    with _langfuse([SAMPLE], CHILDREN, SCORES) as get:
        trace = fetch_question_trace("q1", start, start + timedelta(hours=2))

    assert get.call_args_list[0].kwargs["params"]["name"] == "eval-sample:q1"
    assert (
        get.call_args_list[0].kwargs["params"]["fromStartTime"]
        == "2026-10-26T12:00:00Z"
    )
    assert trace["langfuse_url"].endswith("/project/proj/traces/trace1")
    assert trace["expected_run"] == {
        "sql": "SELECT 1",
        "execution_time_ms": 12.5,
        "success": True,
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
    }
    assert trace["generated_run"] is None
    assert trace["agent_status"] == "error"
    assert trace["metadata"] == {"total_latency_ms": 100}
    assert {s["name"]: s["applicable"] for s in trace["scores"]} == {
        "contains_accuracy": True,
        "time_shift": False,
        "agent_sql_generation_seconds": True,
    }


def test_fetch_question_trace_falls_back_when_v2_is_not_implemented():
    import requests

    not_implemented = MagicMock(status_code=501)
    not_implemented.raise_for_status.side_effect = requests.HTTPError(
        response=not_implemented
    )
    legacy_sample = MagicMock(
        status_code=200, **{"json.return_value": {"data": [SAMPLE]}}
    )
    legacy_trace = MagicMock(
        status_code=200,
        **{"json.return_value": {"observations": CHILDREN, "scores": SCORES}},
    )
    start = datetime(2026, 10, 26, 12, 0)
    with patch(
        "app.services.eval_question_trace.requests.get",
        side_effect=[not_implemented, legacy_sample, legacy_trace],
    ) as get:
        trace = fetch_question_trace("q1", start, start + timedelta(hours=2))

    urls = [c.args[0] for c in get.call_args_list]
    assert urls[1].endswith("/api/public/observations")
    assert urls[2].endswith("/api/public/traces/trace1")
    assert trace["expected_run"]["row_count"] == 1
    assert trace["agent_status"] == "error"
    assert {s["name"] for s in trace["scores"]} >= {"contains_accuracy", "time_shift"}


def test_fetch_question_trace_without_a_record():
    now = datetime(2026, 10, 26, 12, 0)
    with _langfuse([]):
        assert fetch_question_trace("q1", now, now) is None


def test_fetch_question_trace_retries_when_rate_limited():
    limited = MagicMock(status_code=429)
    empty = MagicMock(status_code=200, **{"json.return_value": {"data": []}})
    now = datetime(2026, 10, 26, 12, 0)
    with (
        patch("app.services.eval_question_trace.time.sleep"),
        patch(
            "app.services.eval_question_trace.requests.get",
            side_effect=[limited, empty],
        ) as get,
    ):
        assert fetch_question_trace("q1", now, now) is None
    assert get.call_count == 2


def _finished_question(db_session):
    question = GoldenQuestion(
        connection_id=next(_next_connection), question="q?", expected_sql="SELECT 1"
    )
    run = EvalRun(table_id=None, total_questions=1, status=EvalStatus.completed)
    db_session.add_all([question, run])
    db_session.commit()
    db_session.refresh(question)
    db_session.refresh(run)
    db_session.add(
        EvalResult(
            run_id=run.id,
            question_id=question.id,
            score=0.0,
            status="fail",
            error_type="boom",
        )
    )
    db_session.commit()
    return run, question


def test_question_endpoint_returns_trace(client, db_session):
    run, question = _finished_question(db_session)
    with (
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}.fetch_question_trace", return_value={"trace_id": "t"}),
    ):
        langfuse.enabled = True
        body = client.get(
            f"/api/evaluations/runs/{run.id}/questions/{question.id}"
        ).json()
    assert body["question"] == "q?"
    assert body["status"] == "fail"
    assert body["trace"] == {"trace_id": "t"}
    assert body["trace_unavailable_reason"] is None


def test_question_endpoint_still_answers_when_langfuse_fails(client, db_session):
    import requests

    run, question = _finished_question(db_session)
    with (
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(
            f"{ORCH}.fetch_question_trace", side_effect=requests.ConnectionError("x")
        ),
    ):
        langfuse.enabled = True
        response = client.get(f"/api/evaluations/runs/{run.id}/questions/{question.id}")
    assert response.status_code == 200
    assert response.json()["trace"] is None
    assert response.json()["failure_type"] == "boom"
    assert "Langfuse" in response.json()["trace_unavailable_reason"]


def test_question_endpoint_unknown_question(client, db_session):
    run, _ = _finished_question(db_session)
    assert (
        client.get(f"/api/evaluations/runs/{run.id}/questions/nope").status_code == 404
    )


def test_run_connection_remembers_the_chosen_model(client, db_session, test_engine):
    response, _ = _run_connection(
        client, next(_next_connection), _pairs(["a"]), model="gpt-oss:20b"
    )
    assert response.status_code == 202
    assert response.json()["agent_model"] == "gpt-oss:20b"

    # the pipeline asks the evaluation service for that model
    with (
        patch(f"{ORCH}.engine", test_engine),
        patch(
            f"{ORCH}._call_run_new_evaluation", side_effect=RuntimeError("stop")
        ) as call,
    ):
        from app.routers.orchestration import _run_dataset_pipeline

        _run_dataset_pipeline("ds", response.json()["id"])
    assert call.call_args.args[0]["model"] == "gpt-oss:20b"


def test_run_connection_without_a_model_uses_the_default(client, test_engine):
    response, _ = _run_connection(client, next(_next_connection), _pairs(["a"]))
    assert response.json()["agent_model"] is None
    with (
        patch(f"{ORCH}.engine", test_engine),
        patch(
            f"{ORCH}._call_run_new_evaluation", side_effect=RuntimeError("stop")
        ) as call,
    ):
        from app.routers.orchestration import _run_dataset_pipeline

        _run_dataset_pipeline("ds", response.json()["id"])
    assert "model" not in call.call_args.args[0]
    assert "agent" not in call.call_args.args[0]


def test_run_connection_with_the_previous_agent(client, test_engine):
    response, _ = _run_connection(
        client,
        next(_next_connection),
        _pairs(["a"]),
        agent="previous",
        model="gpt-oss:20b",
    )
    assert response.status_code == 202
    # the previous agent has no model choice
    assert response.json()["agent"] == "previous"
    assert response.json()["agent_model"] is None
    with (
        patch(f"{ORCH}.engine", test_engine),
        patch(
            f"{ORCH}._call_run_new_evaluation", side_effect=RuntimeError("stop")
        ) as call,
    ):
        from app.routers.orchestration import _run_dataset_pipeline

        _run_dataset_pipeline("ds", response.json()["id"])
    assert call.call_args.args[0]["agent"] == "previous"
    assert "model" not in call.call_args.args[0]


def test_run_connection_rejects_an_unknown_agent(client):
    response, _ = _run_connection(
        client, next(_next_connection), _pairs(["a"]), agent="other"
    )
    assert response.status_code == 422


def _agent_session(tool_names, list_result=None):
    session = MagicMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(
        return_value=MagicMock(tools=[MagicMock(name=n) for n in tool_names])
    )
    for tool, name in zip(
        session.list_tools.return_value.tools, tool_names, strict=True
    ):
        tool.name = name
    session.call_tool = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=list_result)])
    )
    return session


def _patch_agent(session):
    class _Ctx:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *exc):
            return False

    return (
        patch(
            "app.services.agent_models.streamablehttp_client",
            return_value=_Ctx((None, None, None)),
        ),
        patch("app.services.agent_models.ClientSession", return_value=_Ctx(session)),
    )


def test_models_endpoint_lists_the_agents_models(client):
    session = _agent_session(
        ["chat_with_agent", "list_models"],
        '{"models": ["gemma4:e4b", "gpt-oss:20b"], "default": "gemma4:e4b"}',
    )
    stream, client_session = _patch_agent(session)
    with stream, client_session:
        body = client.get("/api/evaluations/models").json()
    assert body == {
        "models": ["gemma4:e4b", "gpt-oss:20b"],
        "default": "gemma4:e4b",
        "list_error": None,
    }


def test_models_endpoint_with_an_agent_that_cant_choose(client):
    stream, client_session = _patch_agent(_agent_session(["chat_with_agent"]))
    with stream, client_session:
        body = client.get("/api/evaluations/models").json()
    assert body["models"] == [] and "default" in body["list_error"]


def test_models_endpoint_when_the_agent_is_down(client):
    with patch(
        "app.services.agent_models.streamablehttp_client",
        side_effect=OSError("refused"),
    ):
        body = client.get("/api/evaluations/models").json()
    assert body["models"] == [] and "refused" in body["list_error"]


def _finished_run(db_session, count):
    connection_id = next(_next_connection)
    run = EvalRun(
        connection_id=connection_id,
        status=EvalStatus.completed,
        total_questions=count,
        agent_model="gpt-oss:20b",
    )
    db_session.add(run)
    questions = [
        GoldenQuestion(
            question=f"export q{i}?",
            expected_sql=f"SELECT {i}",
            connection_id=connection_id,
        )
        for i in range(count)
    ]
    db_session.add_all(questions)
    db_session.flush()
    for i, q in enumerate(questions):
        db_session.add(
            EvalResult(
                run_id=run.id,
                question_id=q.id,
                score=float(i % 2),
                status="pass" if i % 2 else "fail",
                scores={"contains_accuracy": float(i % 2)},
            )
        )
    db_session.commit()
    return run, questions


def _trace_for(question_id, *_):
    return {
        "expected_sql": "SELECT 1",
        "generated_sql": "select 1",
        "executed_generated_sql": None,
        "expected_run": {
            "success": True,
            "columns": ["n"],
            "rows": [[1]],
            "row_count": 1,
        },
        "generated_run": {
            "success": False,
            "error": "boom",
            "columns": [],
            "rows": [],
            "row_count": 0,
        },
        "langfuse_url": f"https://langfuse.test/{question_id}",
    }


def test_results_export_pages_through_a_run(client, db_session):
    run, questions = _finished_run(db_session, 3)
    with (
        patch(f"{ORCH}.langfuse_client") as langfuse,
        patch(f"{ORCH}.fetch_question_trace", side_effect=_trace_for),
    ):
        langfuse.enabled = True
        first = client.get(
            f"/api/evaluations/runs/{run.id}/results", params={"limit": 2}
        )
        second = client.get(
            f"/api/evaluations/runs/{run.id}/results",
            params={"limit": 2, "offset": first.json()["next_offset"]},
        )

    page = first.json()
    assert page["total"] == 3 and page["next_offset"] == 2
    assert page["agent_model"] == "gpt-oss:20b"
    assert second.json()["next_offset"] is None
    exported = page["results"] + second.json()["results"]
    assert sorted(r["question_id"] for r in exported) == sorted(q.id for q in questions)

    row = exported[0]
    assert row["golden_sql"] == "SELECT 1" and row["generated_sql"] == "select 1"
    assert row["golden_result"] == {
        "success": True,
        "error": None,
        "columns": ["n"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
    }
    assert row["generated_result"]["error"] == "boom"
    assert row["details_unavailable_reason"] is None


def test_results_export_without_langfuse_still_lists_the_questions(client, db_session):
    run, _ = _finished_run(db_session, 1)
    with patch(f"{ORCH}.langfuse_client") as langfuse:
        langfuse.enabled = False
        row = client.get(f"/api/evaluations/runs/{run.id}/results").json()["results"][0]
    assert row["golden_sql"] == "SELECT 0"
    assert row["generated_sql"] is None and row["golden_result"] is None
    assert row["details_unavailable_reason"] == "Langfuse is not configured."


def test_results_export_unknown_run(client):
    assert client.get("/api/evaluations/runs/nope/results").status_code == 404


def test_export_api_needs_its_key(client, db_session):
    run, _ = _finished_run(db_session, 1)
    url = f"/api/export/evaluations/runs/{run.id}/results"
    with patch("app.routers.export.settings.EVAL_EXPORT_API_KEY", ""):
        assert client.get(url).status_code == 503  # off until a key is set
    with patch("app.routers.export.settings.EVAL_EXPORT_API_KEY", "secret-key"):
        assert client.get(url).status_code == 401
        assert (
            client.get(url, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        with patch(f"{ORCH}.langfuse_client") as langfuse:
            langfuse.enabled = False
            ok = client.get(url, headers={"Authorization": "Bearer secret-key"})
            runs = client.get(
                "/api/export/evaluations/runs",
                params={"connection_id": run.connection_id},
                headers={"Authorization": "Bearer secret-key"},
            )
    assert ok.status_code == 200 and ok.json()["total"] == 1
    assert [r["id"] for r in runs.json()] == [run.id]


def _result(scores, score=0.0):
    return EvalResult(
        run_id="r", question_id="q", score=score, status="fail", scores=scores
    )


def test_question_score_prefers_time_shift():
    from app.services.run_score import question_score

    assert question_score(_result({"contains_accuracy": 1.0, "time_shift": 0.6})) == (
        0.6,
        "time_shift",
    )
    assert question_score(_result({"contains_accuracy": 0.0})) == (
        0.0,
        "contains_accuracy",
    )
    assert question_score(_result(None, score=1.0)) == (
        1.0,
        "contains_accuracy",
    )  # old result
    assert question_score(_result({"sql_exact_match": 0.0})) == (None, None)


def test_run_score_averages_and_explains():
    from app.services.run_score import run_score

    score, breakdown = run_score(
        [
            _result({"contains_accuracy": 1.0, "time_shift": 0.8}),
            _result({"contains_accuracy": 1.0, "time_shift": 0.4}),
            _result({"contains_accuracy": 1.0}),
            _result({"contains_accuracy": 0.0}),
            _result({}),
        ]
    )
    assert score == round((0.8 + 0.4 + 1.0 + 0.0) / 4, 4)
    assert breakdown == {
        "questions": 4,
        "time_shift": {"count": 2, "average": 0.6},
        "contains_accuracy": {"count": 2, "average": 0.5},
        "unscored": 1,
    }
    assert run_score([]) == (
        None,
        {
            "questions": 0,
            "time_shift": {"count": 0, "average": None},
            "contains_accuracy": {"count": 0, "average": None},
            "unscored": 0,
        },
    )


def test_report_and_run_list_carry_the_score(client, db_session):
    run, questions = _finished_run(db_session, 2)
    # q0 fails contains (0), q1 passes contains (1) — give q1 a time shift of 0.5
    result = db_session.exec(
        __import__("sqlmodel")
        .select(EvalResult)
        .where(EvalResult.question_id == questions[1].id)
    ).one()
    result.scores = {"contains_accuracy": 1.0, "time_shift": 0.5}
    db_session.add(result)
    db_session.commit()

    report = client.get(f"/api/evaluations/runs/{run.id}/report").json()
    assert report["score"] == 0.25
    assert report["score_breakdown"]["time_shift"] == {"count": 1, "average": 0.5}
    by_id = {q["question_id"]: q for q in report["per_question"]}
    assert by_id[questions[1].id]["final_score"] == 0.5
    assert by_id[questions[1].id]["score_source"] == "time_shift"
    assert by_id[questions[0].id]["score_source"] == "contains_accuracy"

    listed = client.get(
        "/api/evaluations/runs", params={"connection_id": run.connection_id}
    )
    assert listed.json()[0]["final_score"] == 0.25


def _with_cloud_fallback():
    s = "app.services.eval_question_trace.settings"
    return (
        patch(f"{s}.LANGFUSE_HOST", "https://langfuse.private"),
        patch(f"{s}.LANGFUSE_FALLBACK_HOST", "https://cloud.langfuse.com"),
        patch(f"{s}.LANGFUSE_FALLBACK_PUBLIC_KEY", "pk"),
        patch(f"{s}.LANGFUSE_FALLBACK_SECRET_KEY", "sk"),
        patch("app.services.eval_question_trace.time.sleep"),
    )


def _page(data):
    return MagicMock(status_code=200, **{"json.return_value": {"data": data}})


def test_cloud_is_read_when_the_private_langfuse_is_unreachable():
    import requests

    start = datetime(2026, 10, 26, 12, 0)
    down = requests.ConnectionError("Name or service not known")
    host, fb_host, pk, sk, sleep = _with_cloud_fallback()
    with (
        host,
        fb_host,
        pk,
        sk,
        sleep,
        patch(
            "app.services.eval_question_trace.requests.get",
            side_effect=[
                down,
                down,
                down,
                _page([SAMPLE]),
                _page(CHILDREN),
                _page(SCORES),
            ],
        ) as get,
    ):
        trace = fetch_question_trace("q1", start, start + timedelta(hours=2))

    hosts = [c.args[0].split("/api/")[0] for c in get.call_args_list]
    assert hosts[:3] == ["https://langfuse.private"] * 3  # retried, then gave up
    assert set(hosts[3:]) == {"https://cloud.langfuse.com"}
    assert get.call_args_list[3].kwargs["auth"] == ("pk", "sk")
    assert trace["langfuse_url"].startswith("https://cloud.langfuse.com/project/proj/")


def test_cloud_is_read_when_the_private_langfuse_has_no_record():
    start = datetime(2026, 10, 26, 12, 0)
    host, fb_host, pk, sk, sleep = _with_cloud_fallback()
    with (
        host,
        fb_host,
        pk,
        sk,
        sleep,
        patch(
            "app.services.eval_question_trace.requests.get",
            side_effect=[_page([]), _page([SAMPLE]), _page(CHILDREN), _page(SCORES)],
        ),
    ):
        trace = fetch_question_trace("q1", start, start + timedelta(hours=2))
    assert trace["trace_id"] == "trace1"


def test_no_record_anywhere_is_none_and_all_down_raises():
    import requests

    start = datetime(2026, 10, 26, 12, 0)
    host, fb_host, pk, sk, sleep = _with_cloud_fallback()
    with host, fb_host, pk, sk, sleep:
        with patch(
            "app.services.eval_question_trace.requests.get",
            side_effect=[_page([]), _page([])],
        ):
            assert fetch_question_trace("q1", start, start) is None
        with patch(
            "app.services.eval_question_trace.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            with pytest.raises(requests.ConnectionError):
                fetch_question_trace("q1", start, start)


def test_no_fallback_without_its_keys():
    from app.services.eval_question_trace import langfuse_sources

    with patch(
        "app.services.eval_question_trace.settings.LANGFUSE_FALLBACK_PUBLIC_KEY", ""
    ):
        assert len(langfuse_sources()) == 1
