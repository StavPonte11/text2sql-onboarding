"""
Latency / cost benchmark for the Deep Agent.

    uv run deep-agent-bench                                    # benchmarks/deep_agent_questions.json
    uv run deep-agent-bench --questions my.json --out runs/bench.json --concurrency 2

Reports per question and in aggregate: wall-clock latency (p50/p95), model calls, tool
calls, SQL executions, input/output tokens (from provider usage metadata) and the answer
verification status. Accuracy against gold SQL is the evaluation harness's job (#44).
"""

import argparse
import asyncio
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.config import settings
from deep_agent import observability
from deep_agent.answer import best_effort_result
from deep_agent.context import DeepAgentContext

DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "benchmarks" / "deep_agent_questions.json"


@dataclass(frozen=True)
class QuestionRun:
    id: str
    question: str
    latency_s: float
    model_calls: int
    tool_calls: int
    sql_executions: int
    input_tokens: int
    output_tokens: int
    status: str
    verified: bool
    error: str | None = None


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0–100)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _token_usage(messages: list) -> tuple[int, int]:
    inp = out = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None) or {}
        inp += int(usage.get("input_tokens") or 0)
        out += int(usage.get("output_tokens") or 0)
    return inp, out


async def run_question(agent, item: dict[str, Any], context: DeepAgentContext, timeout_s: float) -> QuestionRun:
    thread_id = f"bench_{item['id']}_{uuid.uuid4().hex[:6]}"
    config = observability.build_run_config(thread_id, connection_id=context.connection_id)
    start = time.perf_counter()
    error = None
    try:
        async with asyncio.timeout(timeout_s):
            state = await agent.ainvoke({"messages": [("user", item["question"])]}, config, context=context)
        final_result = state.get("final_result") or {"status": "no_answer", "verified": False}
    except TimeoutError:
        state = (await agent.aget_state(config)).values
        final_result = best_effort_result(state.get("executed_queries") or [], f"timeout after {timeout_s:.0f}s")
        error = "timeout"
    except Exception as exc:  # one failing question must not abort the benchmark
        state, final_result, error = {"messages": []}, {"status": "error", "verified": False}, repr(exc)
    latency = time.perf_counter() - start

    messages = state.get("messages") or []
    metrics = observability.run_metrics(messages, latency, final_result)
    observability.score_run(observability.langfuse_trace_id(config), metrics)
    input_tokens, output_tokens = _token_usage(messages)
    return QuestionRun(
        id=item["id"], question=item["question"], latency_s=metrics.latency_s, model_calls=metrics.model_calls,
        tool_calls=metrics.tool_calls, sql_executions=metrics.sql_executions, input_tokens=input_tokens,
        output_tokens=output_tokens, status=final_result.get("status", "no_answer"),
        verified=bool(final_result.get("verified")), error=error,
    )


async def run_benchmark(
    agent, questions: list[dict[str, Any]], context: DeepAgentContext, concurrency: int = 1,
    timeout_s: float | None = None,
) -> list[QuestionRun]:
    sem = asyncio.Semaphore(max(1, concurrency))
    timeout_s = timeout_s or settings.DEEP_AGENT_RUN_TIMEOUT_S

    async def one(item):
        async with sem:
            return await run_question(agent, item, context, timeout_s)

    return list(await asyncio.gather(*(one(q) for q in questions)))


def summarize(runs: list[QuestionRun]) -> dict[str, Any]:
    n = len(runs) or 1
    latencies = [r.latency_s for r in runs]
    return {
        "questions": len(runs),
        "latency_p50_s": round(percentile(latencies, 50), 2),
        "latency_p95_s": round(percentile(latencies, 95), 2),
        "avg_model_calls": round(sum(r.model_calls for r in runs) / n, 2),
        "avg_tool_calls": round(sum(r.tool_calls for r in runs) / n, 2),
        "avg_sql_executions": round(sum(r.sql_executions for r in runs) / n, 2),
        "avg_input_tokens": round(sum(r.input_tokens for r in runs) / n),
        "avg_output_tokens": round(sum(r.output_tokens for r in runs) / n),
        "verified_rate": round(sum(r.verified for r in runs) / n, 3),
        "errors": sum(1 for r in runs if r.error),
    }


def _print_report(runs: list[QuestionRun], summary: dict[str, Any]) -> None:
    print(f"{'id':5} {'latency':>8} {'model':>6} {'tools':>6} {'sql':>4} {'in_tok':>8} {'out_tok':>8}  status")
    for r in runs:
        print(f"{r.id:5} {r.latency_s:8.2f} {r.model_calls:6d} {r.tool_calls:6d} {r.sql_executions:4d} "
              f"{r.input_tokens:8d} {r.output_tokens:8d}  {r.status}{' (' + r.error + ')' if r.error else ''}")
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Deep Agent latency and cost.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--connection-id", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None, help="Write per-question results + summary as JSON.")
    args = parser.parse_args()

    from agent.utils.jeen_metadata_client import close_mcp_sessions
    from deep_agent.agent import create_deep_agent

    questions = json.loads(args.questions.read_text(encoding="utf-8"))

    async def _run():
        try:
            return await run_benchmark(create_deep_agent(), questions, DeepAgentContext(connection_id=args.connection_id),
                                       concurrency=args.concurrency)
        finally:
            await close_mcp_sessions()

    try:
        runs = asyncio.run(_run())
    finally:
        observability.flush()
    summary = summarize(runs)
    _print_report(runs, summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"summary": summary, "runs": [asdict(r) for r in runs]},
                                       ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
