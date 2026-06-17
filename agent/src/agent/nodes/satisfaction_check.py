"""
G2-04: Satisfaction Check Module
=================================
A quality-control gateway node placed between the refiner's success path
and the finalizer.  Runs up to four independent verification checks, each
individually gated by a feature flag read from runtime_flags (G4).

Graph position:
  [refiner: success] → [satisfaction_check]
      → (any check fails, fail_count < MAX) → [refiner]
      → (any check fails, fail_count >= MAX) → [hitl_escalation]
      → (all checks pass / module disabled)   → [finalizer]
"""

from __future__ import annotations

import json
import logging

from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm
from langchain_core.runnables.config import RunnableConfig
from agent.utils.redis_publisher import publish_node_event
from agent.state import AgentState
from agent.utils.schema_enrichment import ColumnCoverageOutput, SemanticAlignmentOutput

logger = logging.getLogger(__name__)


def _f(runtime_flags: dict, name: str, default):
    """Read a flag from runtime_flags, falling back to *default*."""
    return runtime_flags.get(name, default)


async def satisfaction_check_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """
    Multi-stage satisfaction judge.

    Returns a partial state dict.  The conditional edge `route_satisfaction`
    in graph.py inspects `satisfaction_failures` to decide the next node.
    """
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    import asyncio
    asyncio.create_task(publish_node_event(thread_id, "satisfaction_check"))

    runtime_flags = state.get("runtime_flags") or {}

    # ── Global gate ───────────────────────────────────────────────────────────
    check_enabled = _f(runtime_flags, "SATISFACTION_CHECK_ENABLED", settings.SATISFACTION_CHECK_ENABLED)
    if not check_enabled:
        return {"satisfaction_failures": [], "execution_path": ["satisfaction_check"]}

    # ── LLM (used for Check C and D) ──────────────────────────────────────────
    llm = get_llm("satisfaction_check", runtime_flags=runtime_flags)

    failures: list[str] = []
    rows = state.get("inline_result_rows") or []
    columns: list[str] = []

    # Attempt to derive column names from the first result row
    if rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())

    # ── Check A: Execution Success ────────────────────────────────────────────
    if _f(runtime_flags, "SATISFACTION_CHECK_EXECUTION", settings.SATISFACTION_CHECK_EXECUTION):
        if state.get("trino_error"):
            failures.append(f"[CHECK_A] Execution failed: {state['trino_error']}")

    # ── Check B: Row Plausibility ─────────────────────────────────────────────
    if _f(runtime_flags, "SATISFACTION_CHECK_PLAUSIBILITY", settings.SATISFACTION_CHECK_PLAUSIBILITY):
        n = len(rows)
        min_rows = _f(runtime_flags, "SATISFACTION_MIN_ROWS", settings.SATISFACTION_MIN_ROWS)
        max_rows = _f(runtime_flags, "SATISFACTION_MAX_ROWS", settings.SATISFACTION_MAX_ROWS)
        if n < min_rows:
            failures.append(
                f"[CHECK_B] Result returned {n} rows — below minimum {min_rows}."
            )
        elif n > max_rows:
            failures.append(
                f"[CHECK_B] Result returned {n} rows — exceeds maximum {max_rows}."
            )

    # ── Check C: Structural Column Coverage ───────────────────────────────────
    if _f(runtime_flags, "SATISFACTION_CHECK_COLUMNS", settings.SATISFACTION_CHECK_COLUMNS) and columns:
        prompt = (
            f"User question: {state.get('user_query', '')}\n"
            f"SQL column headers returned: {', '.join(columns)}\n\n"
            "Do these column headers conceptually satisfy what the user asked for?"
        )
        try:
            structured = llm.with_structured_output(ColumnCoverageOutput, method="json_schema")
            result: ColumnCoverageOutput = await structured.ainvoke(prompt)
            if not result.satisfies_question:
                failures.append(
                    f"[CHECK_C] Column coverage insufficient: {result.reason}"
                )
        except Exception as exc:
            logger.warning("satisfaction_check Check C failed: %s", exc)

    # ── Check D: Semantic Alignment (LLM judge, scored 0–1) ───────────────────
    check_semantic = _f(runtime_flags, "SATISFACTION_CHECK_SEMANTIC", settings.SATISFACTION_CHECK_SEMANTIC)
    threshold = float(_f(runtime_flags, "SATISFACTION_SEMANTIC_THRESHOLD", settings.SATISFACTION_SEMANTIC_THRESHOLD))
    if check_semantic and columns:
        prompt = (
            f"User question: {state.get('user_query', '')}\n"
            f"SQL generated: {state.get('sql_query', '')}\n"
            f"Result column headers: {', '.join(columns)}\n\n"
            "Score alignment between the question intent and the query output schema (0.0–1.0)."
        )
        try:
            structured = llm.with_structured_output(SemanticAlignmentOutput, method="json_schema")
            result: SemanticAlignmentOutput = await structured.ainvoke(prompt)
            if result.alignment_score < threshold:
                failures.append(
                    f"[CHECK_D] Semantic alignment score {result.alignment_score:.2f} "
                    f"below threshold {threshold}: {result.reason}"
                )
        except Exception as exc:
            logger.warning("satisfaction_check Check D failed: %s", exc)

    # ── Accounting & Langfuse instrumentation ─────────────────────────────────
    prior_fail_count = state.get("satisfaction_fail_count") or 0
    fail_count = prior_fail_count + (1 if failures else 0)

    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client.trace(
                id=trace_id,
                metadata={
                    "satisfaction_failures": failures,
                    "satisfaction_fail_count": fail_count,
                    "satisfaction_checks_run": {
                        "execution": _f(runtime_flags, "SATISFACTION_CHECK_EXECUTION", settings.SATISFACTION_CHECK_EXECUTION),
                        "plausibility": _f(runtime_flags, "SATISFACTION_CHECK_PLAUSIBILITY", settings.SATISFACTION_CHECK_PLAUSIBILITY),
                        "columns": _f(runtime_flags, "SATISFACTION_CHECK_COLUMNS", settings.SATISFACTION_CHECK_COLUMNS),
                        "semantic": check_semantic,
                    },
                },
            )
    except Exception as exc:
        logger.warning("satisfaction_check Langfuse trace failed: %s", exc)

    partial: dict = {
        "satisfaction_failures": failures if failures else None,
        "satisfaction_fail_count": fail_count,
    }

    if failures:
        partial["last_error"] = "; ".join(failures)
        if fail_count >= settings.SATISFACTION_MAX_FAILURES:
            partial["escalation_reason"] = (
                f"Satisfaction checks failed {fail_count} times. "
                f"Last failures: {'; '.join(failures)}"
            )

    return partial
