from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.trino import TrinoExecutionResult
from deep_agent import observability, tools
from deep_agent.agent import create_deep_agent
from deep_agent.observability import RunMetrics, build_run_config, run_metrics


class RunTreeRecorder(BaseCallbackHandler):
    """Records every run's id/parent so we can assert the trace is one tree with no orphans."""

    def __init__(self):
        self.runs: dict[UUID, dict] = {}

    def _add(self, kind, serialized, run_id, parent_run_id, **kwargs):
        name = kwargs.get("name") or (serialized or {}).get("name")
        self.runs[run_id] = {"kind": kind, "name": name, "parent": parent_run_id}

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self._add("chain", serialized, run_id, parent_run_id, **kwargs)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self._add("tool", serialized, run_id, parent_run_id, **kwargs)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self._add("llm", serialized, run_id, parent_run_id, **kwargs)

    def roots(self):
        return [rid for rid, r in self.runs.items() if r["parent"] is None]

    def reaches(self, run_id, root):
        while run_id is not None:
            if run_id == root:
                return True
            run_id = self.runs.get(run_id, {}).get("parent")
        return False


STEPS = [
    ("get_database_catalog", {}),
    ("get_sample_rows", {"table_name": '"postgres"."public"."flights_table"'}),
    ("execute_trino_query", {"sql": "SELECT COUNT(*) FROM flights_table WHERE launch_country = 'France'"}),
    ("enrich_sql_filters", {"sql": "SELECT 1 FROM flights_table WHERE launch_country = 'צרפת'"}),
]


def _policy(messages):
    done = len([m for m in messages if m.type == "tool"])
    if done < len(STEPS):
        name, args = STEPS[done]
        return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"t{done}"}])
    return AIMessage("תשובה\n```sql\nSELECT 1\n```")


@pytest.mark.asyncio
async def test_one_trace_tree_with_no_orphans(policy_model):
    enrichment_llm = GenericFakeChatModel(messages=iter([AIMessage("pick France")]))

    async def fake_enrich(**kwargs):
        # Like the real orchestrator: an LLM call made with the forwarded config.
        await enrichment_llm.ainvoke("choose canonical value", config=kwargs["config"])
        return kwargs["initial_sql"], None, False

    recorder = RunTreeRecorder()
    client = MagicMock(get_catalog_prompt=AsyncMock(return_value="# catalog"))
    trino = TrinoExecutionResult(success=True, rows=[[466]], columns=["c"], row_count=1, query_id="q-1",
                                 execution_time_ms=12)
    agent = create_deep_agent(model=policy_model(_policy))
    config = build_run_config("thread-obs", user_id="u-1", callbacks=[recorder], tracing=False)

    with patch.object(tools, "get_jeen_metadata_client", return_value=client), \
         patch.object(tools, "execute_query_sync", return_value=trino), \
         patch("agent.nodes.refiner.parse_jeen_catalog_tables", return_value=({"t": {}}, ["t"])), \
         patch("agent.services.enrichment_orchestrator.EnrichmentOrchestrator.enrich_query", side_effect=fake_enrich):
        await agent.ainvoke({"messages": [("user", "כמה טיסות")]}, config)

    roots = recorder.roots()
    assert len(roots) == 1, f"expected exactly one root run, got {[recorder.runs[r] for r in roots]}"
    root = roots[0]

    tool_runs = {r["name"] for r in recorder.runs.values() if r["kind"] == "tool"}
    assert {name for name, _ in STEPS} <= tool_runs
    llm_runs = [rid for rid, r in recorder.runs.items() if r["kind"] == "llm"]
    assert len(llm_runs) >= len(STEPS) + 2  # agent turns + the enrichment LLM call
    for rid, run in recorder.runs.items():
        assert recorder.reaches(rid, root), f"orphan run: {run}"


def test_build_run_config_sets_langfuse_metadata():
    handler = object()
    with patch.object(observability, "_langfuse_handler", return_value=handler):
        config = build_run_config("thread-1", user_id="user-7", connection_id=89, tracing=True)
    assert config["configurable"] == {"thread_id": "thread-1"}
    assert config["callbacks"] == [handler]
    assert config["metadata"]["langfuse_session_id"] == "thread-1"
    assert config["metadata"]["langfuse_user_id"] == "user-7"
    assert config["metadata"]["langfuse_tags"] == ["deep-agent"]
    assert config["metadata"]["connection_id"] == 89
    assert config["metadata"]["agent_version"]
    assert config["tags"] == ["deep-agent"]
    assert config["run_name"] == "deep-agent"


def test_build_run_config_without_tracing_has_no_langfuse_handler():
    with patch.object(observability, "_langfuse_handler") as factory:
        config = build_run_config("t", tracing=False)
    factory.assert_not_called()
    assert config["callbacks"] == []


def test_agent_version_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("DEEP_AGENT_VERSION", "b8e27c8")
    assert observability.agent_version() == "b8e27c8"


def test_trino_tools_annotate_the_tool_span():
    ok_res = TrinoExecutionResult(success=True, rows=[[1], [2]], columns=["a"], row_count=2, query_id="q-9",
                                  execution_time_ms=34)
    with patch.object(tools, "execute_query_sync", return_value=ok_res), \
         patch.object(tools, "annotate_current_span") as annotate:
        out = tools.execute_trino_query.invoke({"sql": "SELECT a FROM t", "limit": 1})
    annotate.assert_called_once_with({"trino_query_id": "q-9", "execution_time_ms": 34, "row_count": 1, "truncated": True})
    assert '"execution_time_ms":34' in out and '"query_id":"q-9"' in out

    err = TrinoExecutionResult(success=False, error_message="line 1:8: Column 'x' cannot be resolved",
                               error_name="COLUMN_NOT_FOUND", query_id="q-10", execution_time_ms=5)
    with patch.object(tools, "execute_query_sync", return_value=err), \
         patch.object(tools, "annotate_current_span") as annotate:
        tools.execute_trino_query.invoke({"sql": "SELECT x FROM t"})
    metadata, = annotate.call_args.args
    assert metadata["error_category"] == "COLUMN_NOT_FOUND" and metadata["trino_query_id"] == "q-10"
    assert annotate.call_args.kwargs["level"] == "ERROR"


def test_annotate_is_a_noop_without_active_span():
    observability.annotate_current_span({"x": 1})  # must not raise outside a trace


def test_run_metrics():
    messages = [
        HumanMessage("q"),
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {}, "id": "1"},
                                  {"name": "get_sample_rows", "args": {}, "id": "2"}]),
        ToolMessage('{"ok":false,"data":null,"error":{}}', tool_call_id="1", name="execute_trino_query"),
        ToolMessage('{"ok":true}', tool_call_id="2", name="get_sample_rows"),
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {}, "id": "3"}]),
        ToolMessage('{"ok":true,"data":{},"error":null}', tool_call_id="3", name="execute_trino_query"),
        AIMessage("answer\n```sql\nSELECT 1\n```"),
    ]
    assert run_metrics(messages, 12.345) == RunMetrics(
        model_calls=3, tool_calls=3, sql_executions=2, executed_successfully=True, final_sql_present=True, latency_s=12.35,
    )


def test_score_run_posts_one_score_per_metric():
    fake_client = SimpleNamespace(create_score=MagicMock())
    with patch("agent.langfuse_client.langfuse_client", fake_client):
        observability.score_run("trace-1", RunMetrics(4, 3, 1, True, True, 9.5))
    names = {c.kwargs["name"]: c.kwargs for c in fake_client.create_score.call_args_list}
    assert set(names) == {f"deep-agent.{n}" for n in
                          ("model_calls", "tool_calls", "sql_executions", "executed_successfully", "final_sql_present", "latency_s",
                           "answer_verified")}
    assert names["deep-agent.executed_successfully"]["data_type"] == "BOOLEAN"
    assert all(kw["trace_id"] == "trace-1" for kw in names.values())


def test_score_run_without_trace_is_noop():
    with patch("agent.langfuse_client.langfuse_client") as client:
        observability.score_run(None, RunMetrics(1, 1, 1, True, True, 1.0))
    client.create_score.assert_not_called()


@pytest.mark.asyncio
async def test_real_langfuse_handler_produces_a_single_trace(policy_model):
    """End-to-end through the real Langfuse v4 CallbackHandler, exported to memory (no network)."""
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    langfuse = Langfuse(public_key="pk-lf-unit-test", secret_key="sk-lf-unit-test", host="http://127.0.0.1:9",
                        span_exporter=exporter, flush_at=1, flush_interval=0.01)

    async def fake_enrich(**kwargs):
        return kwargs["initial_sql"], None, False

    trino = TrinoExecutionResult(success=True, rows=[[1]], columns=["c"], row_count=1, query_id="q-77",
                                 execution_time_ms=9)
    agent = create_deep_agent(model=policy_model(_policy))
    with patch.object(observability, "_langfuse_handler", return_value=CallbackHandler(public_key="pk-lf-unit-test")), \
         patch.object(tools, "get_jeen_metadata_client", return_value=MagicMock(get_catalog_prompt=AsyncMock(return_value="# c"))), \
         patch.object(tools, "execute_query_sync", return_value=trino), \
         patch("agent.nodes.refiner.parse_jeen_catalog_tables", return_value=({"t": {}}, ["t"])), \
         patch("agent.services.enrichment_orchestrator.EnrichmentOrchestrator.enrich_query", side_effect=fake_enrich):
        config = build_run_config("thread-lf", user_id="user-lf", tracing=True)
        await agent.ainvoke({"messages": [("user", "q")]}, config)
    langfuse.flush()

    spans = exporter.get_finished_spans()
    assert spans
    assert len({s.context.trace_id for s in spans}) == 1
    assert [s.name for s in spans if s.parent is None] == ["deep-agent"]
    attrs = {k: v for s in spans for k, v in s.attributes.items()}
    assert attrs["session.id"] == "thread-lf"
    assert attrs["user.id"] == "user-lf"
    assert "deep-agent" in attrs["langfuse.trace.tags"]

    trino_span = next(s for s in spans if s.name == "execute_trino_query")
    meta = {k.removeprefix("langfuse.observation.metadata."): v for k, v in trino_span.attributes.items()}
    assert meta["trino_query_id"] == "q-77"
    assert meta["execution_time_ms"] == 9
    assert meta["row_count"] == 1
    assert observability.langfuse_trace_id(config) == format(spans[0].context.trace_id, "032x")
