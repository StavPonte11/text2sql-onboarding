import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from core.trino import TrinoExecutionResult
from deep_agent import evaluation as ev
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext
from deep_agent.evaluation import EvalItem, ItemScore, Prediction, ResultSet, compare_results


# ── comparison ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pred, gold, ordered, expected", [
    ([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]], False, True),                    # order ignored
    ([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]], True, False),                    # order matters
    ([["a", 1]], [[1, "a"]], False, True),                                       # columns permuted
    ([[0.1 + 0.2]], [[0.3]], False, True),                                       # float tolerance
    ([[Decimal("466")]], [["466"]], False, True),                                # decimal vs numeric string
    ([[466]], [[466], [466]], False, False),                                     # row count
    ([[None]], [[None]], False, True),
    ([[1, 2]], [[1]], False, False),                                             # column count
    ([[" France "]], [["France"]], False, True),                                 # whitespace
])
def test_compare_results(pred, gold, ordered, expected):
    ok, reason = compare_results(ResultSet(["x"] * len(pred[0]), pred), ResultSet(["y"] * len(gold[0]), gold), ordered)
    assert ok is expected, reason


def test_duplicates_matter_when_unordered():
    ok, _ = compare_results(ResultSet(["a"], [[1], [1], [2]]), ResultSet(["a"], [[1], [2], [2]]), ordered=False)
    assert not ok


@pytest.mark.parametrize("sql, ordered", [
    ("SELECT a FROM t ORDER BY a", True),
    ("SELECT a FROM (SELECT a FROM t ORDER BY a) x", False),
    ("WITH c AS (SELECT a FROM t) SELECT a FROM c ORDER BY a DESC LIMIT 5", True),
    ("SELECT count(*) FROM t", False),
    ("not sql at all", False),
])
def test_is_ordered(sql, ordered):
    assert ev.is_ordered(sql) is ordered


# ── scoring and summaries ────────────────────────────────────────────────────

GOLD = ResultSet(["n"], [[466]])


@pytest.mark.parametrize("item, prediction, gold, status", [
    (EvalItem("a", "q", None), Prediction("SELECT 1", GOLD, 1.0), None, "no_gold"),
    (EvalItem("a", "q", "SELECT 466"), Prediction("SELECT 1", GOLD, 1.0), None, "gold_error"),
    (EvalItem("a", "q", "SELECT 466"), Prediction(None, None, 1.0, status="no_answer"), GOLD, "no_result"),
    (EvalItem("a", "q", "SELECT 466"), Prediction("SELECT 466", GOLD, 1.0), GOLD, "correct"),
    (EvalItem("a", "q", "SELECT 466"), Prediction("SELECT 1", ResultSet(["n"], [[1]]), 1.0), GOLD, "incorrect"),
])
def test_score_item(item, prediction, gold, status):
    assert ev.score_item(item, prediction, gold, "boom", "deep").status == status


def _score(status, latency, tags=("t",), tokens=100, tools=3):
    return ItemScore("i", "deep", status, "", tags,
                     {"latency_s": latency, "input_tokens": tokens, "output_tokens": tokens // 10, "tool_calls": tools})


def test_summarize():
    s = ev.summarize([_score("correct", 10, ("temporal",)), _score("incorrect", 20, ("temporal", "join")),
                      _score("no_result", 30), _score("no_gold", 40)])
    assert s["scored"] == 3 and s["execution_accuracy"] == 0.333
    assert s["latency_p50_s"] == 20 and s["latency_p95_s"] == 40
    assert s["accuracy_by_tag"] == {"join": 0.0, "t": 0.0, "temporal": 0.5}
    assert s["status_counts"] == {"correct": 1, "incorrect": 1, "no_result": 1, "no_gold": 1}


@pytest.mark.parametrize("deep, legacy, slo, passed", [
    ({"execution_accuracy": 0.8, "latency_p95_s": 40}, {"execution_accuracy": 0.7, "latency_p95_s": 90}, 60, True),
    ({"execution_accuracy": 0.6, "latency_p95_s": 40}, {"execution_accuracy": 0.7, "latency_p95_s": 90}, 60, False),
    ({"execution_accuracy": 0.9, "latency_p95_s": 75}, None, 60, False),
    ({"execution_accuracy": None, "latency_p95_s": 1}, None, 60, False),
])
def test_gate(deep, legacy, slo, passed):
    summaries = {"deep": deep, **({"legacy": legacy} if legacy else {})}
    assert ev.gate(summaries, slo)[0] is passed


def test_report_markdown_has_one_column_per_system():
    md = ev.report_markdown({"deep": ev.summarize([_score("correct", 1)]), "legacy": ev.summarize([_score("incorrect", 2)])})
    assert md.splitlines()[0] == "| metric | deep | legacy |"
    assert "| execution_accuracy | 1.0 | 0.0 |" in md


# ── end to end: both systems on the same items ──────────────────────────────

ITEMS = [
    EvalItem("q1", "כמה טיסות המריאו מצרפת", "SELECT COUNT(DISTINCT flight_id) FROM flights WHERE launch_country = 'France'", ("simple_filter",)),
    EvalItem("q2", "שאלה בלי זהב", None, ("temporal",)),
]


def _deep_policy(messages):
    if not [m for m in messages if m.type == "tool"]:
        return AIMessage("", tool_calls=[{"name": "execute_trino_query", "id": "e",
                                          "args": {"sql": "SELECT COUNT(*) AS n FROM flights WHERE launch_country = 'France'"}}])
    return AIMessage("", tool_calls=[{"name": "DeepAgentAnswer", "id": "f", "args": {
        "sql": "SELECT COUNT(*) AS n FROM flights WHERE launch_country = 'France'", "explanation_he": "x", "confidence": "high"}}])


class FakeLegacyGraph:
    async def ainvoke(self, state, config=None):
        assert state["non_interactive"] is True
        return {"sql_query": "SELECT 1", "inline_result_columns": ["n"], "inline_result_rows": [[1]], "messages": []}


@pytest.mark.asyncio
async def test_evaluate_both_systems(policy_model):
    def trino(sql, **_):
        return TrinoExecutionResult(success=True, rows=[[466]], columns=["n"], row_count=1, query_id="q")

    agent = create_deep_agent(model=policy_model(_deep_policy))
    runners = [ev.DeepAgentRunner(agent, DeepAgentContext(), timeout_s=30), ev.LegacyGraphRunner(graph=FakeLegacyGraph())]
    with patch.object(ev, "execute_query_sync", side_effect=trino), patch.object(tools, "execute_query_sync", side_effect=trino):
        results = await ev.evaluate(ITEMS, runners)

    deep = {s.id: s for s in results["deep"]}
    legacy = {s.id: s for s in results["legacy"]}
    assert deep["q1"].status == "correct" and deep["q2"].status == "no_gold"
    assert legacy["q1"].status == "incorrect" and legacy["q2"].status == "no_gold"
    summaries = {k: ev.summarize(v) for k, v in results.items()}
    assert summaries["deep"]["execution_accuracy"] == 1.0 and summaries["legacy"]["execution_accuracy"] == 0.0
    assert ev.gate(summaries, p95_slo_s=60)[0] is True


@pytest.mark.asyncio
async def test_legacy_sql_only_answers_are_executed():
    class SqlOnlyGraph:
        async def ainvoke(self, state, config=None):
            return {"sql_query": "SELECT 466 AS n", "messages": []}

    with patch.object(ev, "execute_query_sync",
                      return_value=TrinoExecutionResult(success=True, rows=[[466]], columns=["n"], row_count=1)):
        prediction = await ev.LegacyGraphRunner(graph=SqlOnlyGraph()).run(ITEMS[0])
    assert prediction.result.rows == [[466]]


# ── Langfuse experiment adapters ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_langfuse_task_and_evaluator():
    class StubRunner:
        name = "stub"

        async def run(self, item):
            return Prediction("SELECT 466", ResultSet(["n"], [[466]]), 1.0, status="matched")

    output = await ev.make_langfuse_task(StubRunner())(item=SimpleNamespace(id="x", input={"question": "q"}))
    assert output["rows"] == [[466]]
    with patch.object(ev, "execute_query_sync",
                      return_value=TrinoExecutionResult(success=True, rows=[[466]], columns=["n"], row_count=1)):
        good = ev.execution_accuracy_evaluator(input={}, output=output, expected_output={"sql": "SELECT 466"})
    assert good["value"] == 1.0
    assert ev.execution_accuracy_evaluator(input={}, output=output, expected_output=None)["value"] == 0.0


# ── dataset ──────────────────────────────────────────────────────────────────

def test_seed_dataset_is_valid():
    items = ev.load_dataset(ev.DEFAULT_DATASET)
    assert len(items) >= 10
    for item in items:
        assert item.question.strip() and item.tags
        if item.gold_sql:
            ev.sql_guard.parse_read_statement(item.gold_sql)  # gold SQL is read-only and parses


def test_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"items": [{"id": "a", "question": "q"}, {"id": "a", "question": "r"}]}))
    with pytest.raises(ValueError, match="duplicate"):
        ev.load_dataset(path)
