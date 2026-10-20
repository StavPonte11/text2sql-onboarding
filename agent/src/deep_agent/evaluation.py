"""
Offline evaluation: the migration go/no-go gate (legacy LangGraph workflow vs Deep Agent).

    uv run deep-agent-eval                                         # benchmarks/eval_dataset.json, both systems
    uv run deep-agent-eval --systems deep --out runs/eval.json
    uv run deep-agent-eval --gate                                   # exit 1 if the deep agent fails the gate
    uv run deep-agent-eval --langfuse-dataset text2sql-eval         # Langfuse Experiment (linked dataset run)

Metric: **execution accuracy** — the system's result set equals the result of the gold SQL
(row order ignored unless the gold SQL has a top-level ORDER BY; floats compared to 6 decimals;
a column permutation counts as a match). Also reported: p50/p95 latency, avg tokens, avg tool
calls, and accuracy per tag. Items without gold SQL are reported as `no_gold`, never scored.
"""

import argparse
import asyncio
import datetime as dt
import json
import logging
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import sqlglot
from sqlglot import exp

from agent.config import settings
from agent.utils import sql_guard
from core.trino import execute_query_sync
from deep_agent.benchmark import percentile, run_question_detailed
from deep_agent.context import DeepAgentContext

logger = logging.getLogger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "benchmarks" / "eval_dataset.json"
EVAL_MAX_ROWS = 1000  # gold and predicted result sets are compared up to this many rows


# ── dataset ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvalItem:
    id: str
    question: str
    gold_sql: str | None = None
    tags: tuple[str, ...] = ()


def load_dataset(path: Path) -> list[EvalItem]:
    """Load `{"items": [...]}` (or a bare list) of {id, question, gold_sql?, tags?}."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["items"] if isinstance(raw, dict) else raw
    items = [EvalItem(id=r["id"], question=r["question"], gold_sql=r.get("gold_sql"), tags=tuple(r.get("tags", [])))
             for r in rows]
    if len({i.id for i in items}) != len(items):
        raise ValueError(f"duplicate item ids in {path}")
    return items


# ── result-set comparison ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResultSet:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


def _norm(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        f = float(value)
        if math.isnan(f):
            return "NaN"
        return round(f, 6)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    text = str(value).strip()
    try:  # numbers that came back as strings (e.g. DECIMAL serialised by JSON)
        return round(float(text), 6)
    except ValueError:
        return text


def is_ordered(sql: str) -> bool:
    """True if the query's top-level statement has an ORDER BY (row order is then part of the answer)."""
    try:
        tree = sqlglot.parse_one(sql, read="trino")
    except sqlglot.errors.SqlglotError:
        return False
    return bool(tree.args.get("order")) if isinstance(tree, exp.Query) else False


def compare_results(predicted: ResultSet, gold: ResultSet, ordered: bool) -> tuple[bool, str]:
    pred_rows = [tuple(_norm(v) for v in r) for r in predicted.rows]
    gold_rows = [tuple(_norm(v) for v in r) for r in gold.rows]
    if len(pred_rows) != len(gold_rows):
        return False, f"row count {len(pred_rows)} != gold {len(gold_rows)}"
    if pred_rows and gold_rows and len(pred_rows[0]) != len(gold_rows[0]):
        return False, f"column count {len(pred_rows[0])} != gold {len(gold_rows[0])}"
    if ordered and pred_rows == gold_rows:
        return True, "match"
    if not ordered and Counter(pred_rows) == Counter(gold_rows):
        return True, "match"
    # Same values with columns in a different order still answer the question.
    key = (lambda r: tuple(sorted(r, key=repr)))
    permuted_pred = [key(r) for r in pred_rows]
    permuted_gold = [key(r) for r in gold_rows]
    if (ordered and permuted_pred == permuted_gold) or (not ordered and Counter(permuted_pred) == Counter(permuted_gold)):
        return True, "match (columns permuted)"
    return False, "different values" + (" (order matters)" if ordered else "")


def execute_for_eval(sql: str) -> tuple[ResultSet | None, str | None]:
    try:
        guarded = sql_guard.prepare(sql, EVAL_MAX_ROWS)
    except sql_guard.GuardError as e:
        return None, f"rejected: {e}"
    res = execute_query_sync(guarded.sql, reuse_connection=True)
    if not res.success:
        return None, res.error_message
    rows = res.rows or []
    return ResultSet(res.columns, rows[:EVAL_MAX_ROWS], len(rows) > EVAL_MAX_ROWS), None


# ── systems under test ───────────────────────────────────────────────────────

@dataclass
class Prediction:
    sql: str | None
    result: ResultSet | None
    latency_s: float
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = ""
    error: str | None = None
    trace_id: str | None = None


class Runner(Protocol):
    name: str

    async def run(self, item: EvalItem) -> Prediction: ...


class DeepAgentRunner:
    name = "deep"

    def __init__(self, agent, context: DeepAgentContext, timeout_s: float | None = None) -> None:
        self.agent, self.context = agent, context
        self.timeout_s = timeout_s or settings.DEEP_AGENT_RUN_TIMEOUT_S

    async def run(self, item: EvalItem) -> Prediction:
        run, final_result, trace_id = await run_question_detailed(
            self.agent, {"id": item.id, "question": item.question}, self.context, self.timeout_s
        )
        result = final_result.get("result")
        return Prediction(
            sql=final_result.get("sql"),
            result=ResultSet(result["columns"], result["rows"], result.get("truncated", False)) if result else None,
            latency_s=run.latency_s, model_calls=run.model_calls, tool_calls=run.tool_calls,
            input_tokens=run.input_tokens, output_tokens=run.output_tokens, status=run.status, error=run.error,
            trace_id=trace_id,
        )


class LegacyGraphRunner:
    """The pre-migration LangGraph workflow, invoked like /chat does (non-interactive)."""

    name = "legacy"

    def __init__(self, graph=None) -> None:
        if graph is None:
            from agent.graph import agent_graph as graph
        self.graph = graph

    async def run(self, item: EvalItem) -> Prediction:
        config = {"configurable": {"thread_id": f"eval_legacy_{item.id}_{time.time_ns()}"}}
        start = time.perf_counter()
        try:
            state = await self.graph.ainvoke({
                "user_query": item.question, "allowed_tables": None, "allowed_statuses": None,
                "active_extractors": [], "non_interactive": True,
            }, config=config)
        except Exception as exc:
            return Prediction(None, None, time.perf_counter() - start, status="error", error=repr(exc))
        latency = time.perf_counter() - start
        sql = state.get("sql_query") or None
        result = None
        if state.get("inline_result_columns") is not None and state.get("inline_result_rows") is not None:
            result = ResultSet(state["inline_result_columns"], state["inline_result_rows"])
        elif sql:  # the legacy graph may only return SQL; execute it for a like-for-like comparison
            result, _ = execute_for_eval(sql)
        messages = state.get("messages") or []
        return Prediction(sql, result, latency, model_calls=sum(1 for m in messages if m.type == "ai"),
                          status="answered" if sql else "no_sql")


# ── scoring ──────────────────────────────────────────────────────────────────

@dataclass
class ItemScore:
    id: str
    system: str
    status: str            # correct | incorrect | no_gold | gold_error | no_result
    reason: str
    tags: tuple[str, ...]
    prediction: dict[str, Any] = field(default_factory=dict)


def score_item(item: EvalItem, prediction: Prediction, gold: ResultSet | None, gold_error: str | None,
               system: str) -> ItemScore:
    pred = {k: v for k, v in asdict(prediction).items() if k != "result"}
    pred["row_count"] = len(prediction.result.rows) if prediction.result else None
    if not item.gold_sql:
        return ItemScore(item.id, system, "no_gold", "item has no gold SQL", item.tags, pred)
    if gold is None:
        return ItemScore(item.id, system, "gold_error", f"gold SQL failed: {gold_error}", item.tags, pred)
    if prediction.result is None:
        return ItemScore(item.id, system, "no_result", prediction.error or prediction.status or "no result", item.tags, pred)
    ok, reason = compare_results(prediction.result, gold, ordered=is_ordered(item.gold_sql))
    return ItemScore(item.id, system, "correct" if ok else "incorrect", reason, item.tags, pred)


def summarize(scores: list[ItemScore]) -> dict[str, Any]:
    scored = [s for s in scores if s.status in ("correct", "incorrect", "no_result")]
    correct = sum(s.status == "correct" for s in scored)
    latencies = [s.prediction.get("latency_s") or 0.0 for s in scores]
    n = len(scores) or 1
    by_tag: dict[str, list[int]] = {}
    for s in scored:
        for tag in s.tags:
            by_tag.setdefault(tag, [0, 0])
            by_tag[tag][0] += s.status == "correct"
            by_tag[tag][1] += 1
    return {
        "items": len(scores),
        "scored": len(scored),
        "execution_accuracy": round(correct / len(scored), 3) if scored else None,
        "latency_p50_s": round(percentile(latencies, 50), 2),
        "latency_p95_s": round(percentile(latencies, 95), 2),
        "avg_input_tokens": round(sum(s.prediction.get("input_tokens") or 0 for s in scores) / n),
        "avg_output_tokens": round(sum(s.prediction.get("output_tokens") or 0 for s in scores) / n),
        "avg_tool_calls": round(sum(s.prediction.get("tool_calls") or 0 for s in scores) / n, 2),
        "accuracy_by_tag": {t: round(c / total, 3) for t, (c, total) in sorted(by_tag.items())},
        "status_counts": dict(Counter(s.status for s in scores)),
    }


async def evaluate(items: list[EvalItem], runners: list[Runner], concurrency: int = 1) -> dict[str, list[ItemScore]]:
    gold_cache: dict[str, tuple[ResultSet | None, str | None]] = {}
    for item in items:
        if item.gold_sql:
            gold_cache[item.id] = await asyncio.to_thread(execute_for_eval, item.gold_sql)

    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(runner: Runner, item: EvalItem) -> ItemScore:
        async with sem:
            prediction = await runner.run(item)
        gold, gold_error = gold_cache.get(item.id, (None, None))
        score = score_item(item, prediction, gold, gold_error, runner.name)
        if runner.name == "deep" and prediction.trace_id and score.status in ("correct", "incorrect", "no_result"):
            _score_trace(prediction.trace_id, score)
        return score

    results: dict[str, list[ItemScore]] = {}
    for runner in runners:
        results[runner.name] = list(await asyncio.gather(*(one(runner, item) for item in items)))
    return results


def _score_trace(trace_id: str, score: ItemScore) -> None:
    try:
        from agent.langfuse_client import langfuse_client

        langfuse_client.create_score(trace_id=trace_id, name="deep-agent.execution_accuracy",
                                     value=1.0 if score.status == "correct" else 0.0, data_type="BOOLEAN",
                                     comment=score.reason)
    except Exception as exc:
        logger.debug("execution_accuracy score skipped: %s", exc)


# ── go / no-go ───────────────────────────────────────────────────────────────

def gate(summaries: dict[str, dict[str, Any]], p95_slo_s: float) -> tuple[bool, list[str]]:
    """Deep agent passes if its accuracy >= legacy's (when legacy ran) and its p95 latency <= the SLO."""
    reasons = []
    deep = summaries.get("deep")
    if not deep or deep["execution_accuracy"] is None:
        return False, ["deep agent has no scored items"]
    legacy = summaries.get("legacy")
    if legacy and legacy["execution_accuracy"] is not None and deep["execution_accuracy"] < legacy["execution_accuracy"]:
        reasons.append(f"accuracy {deep['execution_accuracy']} < legacy {legacy['execution_accuracy']}")
    if deep["latency_p95_s"] > p95_slo_s:
        reasons.append(f"p95 latency {deep['latency_p95_s']}s > SLO {p95_slo_s}s")
    return not reasons, reasons


def report_markdown(summaries: dict[str, dict[str, Any]]) -> str:
    keys = ["scored", "execution_accuracy", "latency_p50_s", "latency_p95_s", "avg_tool_calls",
            "avg_input_tokens", "avg_output_tokens"]
    systems = list(summaries)
    lines = ["| metric | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    lines += [f"| {k} | " + " | ".join(str(summaries[s].get(k)) for s in systems) + " |" for k in keys]
    return "\n".join(lines)


# ── Langfuse Experiments ─────────────────────────────────────────────────────

def make_langfuse_task(runner: Runner):
    """Experiment task: item.input = {"question": ...} (or a plain string)."""
    async def task(*, item, **kwargs):
        question = item.input["question"] if isinstance(item.input, dict) else str(item.input)
        prediction = await runner.run(EvalItem(id=str(getattr(item, "id", "")), question=question))
        return {"sql": prediction.sql, "status": prediction.status,
                "columns": prediction.result.columns if prediction.result else None,
                "rows": prediction.result.rows if prediction.result else None}
    return task


def execution_accuracy_evaluator(*, input, output, expected_output=None, **kwargs):
    """Experiment evaluator: expected_output = {"sql": gold_sql} (or the gold SQL string)."""
    gold_sql = expected_output.get("sql") if isinstance(expected_output, dict) else expected_output
    if not gold_sql:
        return {"name": "execution_accuracy", "value": 0.0, "comment": "no gold SQL"}
    gold, error = execute_for_eval(gold_sql)
    if gold is None:
        return {"name": "execution_accuracy", "value": 0.0, "comment": f"gold SQL failed: {error}"}
    if not output or output.get("rows") is None:
        return {"name": "execution_accuracy", "value": 0.0, "comment": f"no result ({(output or {}).get('status')})"}
    ok, reason = compare_results(ResultSet(output["columns"], output["rows"]), gold, ordered=is_ordered(gold_sql))
    return {"name": "execution_accuracy", "value": 1.0 if ok else 0.0, "comment": reason}


def run_langfuse_experiment(dataset_name: str, runner: Runner, run_name: str | None = None):
    from agent.langfuse_client import langfuse_client

    dataset = langfuse_client.get_dataset(dataset_name)
    return dataset.run_experiment(
        name=f"{runner.name}-agent", run_name=run_name, task=make_langfuse_task(runner),
        evaluators=[execution_accuracy_evaluator], max_concurrency=2,
        metadata={"system": runner.name},
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Deep Agent against gold SQL (and the legacy graph).")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--langfuse-dataset", default=None, help="Run as a Langfuse Experiment on this dataset instead.")
    parser.add_argument("--systems", default="deep,legacy", help="Comma-separated: deep, legacy.")
    parser.add_argument("--connection-id", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--gate", action="store_true", help="Exit 1 if the deep agent fails the go/no-go gate.")
    parser.add_argument("--p95-slo", type=float, default=60.0, help="p95 latency SLO in seconds for --gate.")
    args = parser.parse_args()

    from agent.utils.jeen_metadata_client import close_mcp_sessions
    from deep_agent import observability
    from deep_agent.agent import acreate_deep_agent

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    context = DeepAgentContext(connection_id=args.connection_id)

    if args.langfuse_dataset:
        async def _deep():
            return DeepAgentRunner(await acreate_deep_agent(), context)
        runner = asyncio.run(_deep()) if "deep" in systems else LegacyGraphRunner()
        result = run_langfuse_experiment(args.langfuse_dataset, runner)
        print(result.format())
        observability.flush()
        return

    items = load_dataset(args.dataset)

    async def _run():
        runners: list[Runner] = []
        if "deep" in systems:
            runners.append(DeepAgentRunner(await acreate_deep_agent(), context))
        if "legacy" in systems:
            runners.append(LegacyGraphRunner())
        try:
            return await evaluate(items, runners, args.concurrency)
        finally:
            await close_mcp_sessions()

    try:
        results = asyncio.run(_run())
    finally:
        observability.flush()
    summaries = {name: summarize(scores) for name, scores in results.items()}
    print(report_markdown(summaries))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "summary": summaries,
            "items": {name: [asdict(s) for s in scores] for name, scores in results.items()},
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.gate:
        passed, reasons = gate(summaries, args.p95_slo)
        print("GATE:", "PASS" if passed else "FAIL — " + "; ".join(reasons))
        raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
