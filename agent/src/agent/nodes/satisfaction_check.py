"""
G2-04: Satisfaction Check Module
=================================
A quality-control gateway node placed between the refiner's success path
and the finalizer.  Runs up to four independent verification checks, each
individually gated by a feature flag.

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
from agent.state import AgentState
from agent.utils.schema_enrichment import ColumnCoverageOutput, SemanticAlignmentOutput

logger = logging.getLogger(__name__)

llm = get_llm("satisfaction_check")


async def satisfaction_check_node(state: AgentState) -> dict:
    """
    Multi-stage satisfaction judge.

    Returns a partial state dict.  The conditional edge `route_satisfaction`
    in graph.py inspects `satisfaction_failures` to decide the next node.
    """
    # ── Global gate ───────────────────────────────────────────────────────────
    if not settings.SATISFACTION_CHECK_ENABLED:
        return {}  # route_satisfaction will forward directly to finalizer

    failures: list[str] = []
    rows = state.get("inline_result_rows") or []
    columns: list[str] = []

    # Attempt to derive column names from the first result row
    if rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())

    # ── Check A: Execution Success ────────────────────────────────────────────
    if settings.SATISFACTION_CHECK_EXECUTION:
        if state.get("trino_error"):
            failures.append(f"[CHECK_A] Execution failed: {state['trino_error']}")

    # ── Check B: Row Plausibility ─────────────────────────────────────────────
    if settings.SATISFACTION_CHECK_PLAUSIBILITY:
        n = len(rows)
        if n < settings.SATISFACTION_MIN_ROWS:
            failures.append(
                f"[CHECK_B] Result returned {n} rows — below minimum {settings.SATISFACTION_MIN_ROWS}."
            )
        elif n > settings.SATISFACTION_MAX_ROWS:
            failures.append(
                f"[CHECK_B] Result returned {n} rows — exceeds maximum {settings.SATISFACTION_MAX_ROWS}."
            )

    # ── Check C: Structural Column Coverage ───────────────────────────────────
    if settings.SATISFACTION_CHECK_COLUMNS and columns:
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
    if settings.SATISFACTION_CHECK_SEMANTIC and columns:
        prompt = (
            f"User question: {state.get('user_query', '')}\n"
            f"SQL generated: {state.get('sql_query', '')}\n"
            f"Result column headers: {', '.join(columns)}\n\n"
            "Score alignment between the question intent and the query output schema (0.0–1.0)."
        )
        try:
            structured = llm.with_structured_output(SemanticAlignmentOutput, method="json_schema")
            result: SemanticAlignmentOutput = await structured.ainvoke(prompt)
            if result.alignment_score < settings.SATISFACTION_SEMANTIC_THRESHOLD:
                failures.append(
                    f"[CHECK_D] Semantic alignment score {result.alignment_score:.2f} "
                    f"below threshold {settings.SATISFACTION_SEMANTIC_THRESHOLD}: {result.reason}"
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
                        "execution": settings.SATISFACTION_CHECK_EXECUTION,
                        "plausibility": settings.SATISFACTION_CHECK_PLAUSIBILITY,
                        "columns": settings.SATISFACTION_CHECK_COLUMNS,
                        "semantic": settings.SATISFACTION_CHECK_SEMANTIC,
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
