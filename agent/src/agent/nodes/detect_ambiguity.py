"""
detect_ambiguity — Pre-SQL Ambiguity Detection Node
====================================================

WHY THIS NODE SITS PRE-SQL (AFTER SCHEMA EXPLORER, BEFORE QUERY BUILDER):
--------------------------------------------------------------------------
The ambiguity-detection prompt requires three inputs:

  1. The original user request   — always available
  2. The database schema         — available from state["jeen_catalog"] after
                                   schema_explorer runs
  3. The agent's current plan    — the schema_plan produced by schema_explorer
                                   describes WHICH tables and columns the agent
                                   intends to use, making it a rich "proposed
                                   interpretation" that the prompt's Agent Proposal
                                   Audit (Step 4) can meaningfully evaluate.

Using schema_plan (rather than waiting for actual SQL) means:
  - Ambiguity is caught BEFORE query_builder runs (saves 1 LLM call)
  - Ambiguity is caught BEFORE Trino executes (saves the DB round-trip)
    because it spells out the reasoning in plain language.
  - False positives from SQL syntax errors are impossible (no SQL exists yet).

NOTE: The graph was reordered so detect_ambiguity runs AFTER query_builder.
Now, it evaluates BOTH the `sql_query` and the `sql_explanation` to see if
the query builder had to guess or drop a filter.

If a query is genuinely ambiguous, we short-circuit the entire expensive
pipeline — no refiner, no Trino — and return a clarifying
question to the user immediately.

Graph position (main agent graph):
  sql_static_validations → detect_ambiguity
      ├─ [clear]        → query_builder → refiner_subagent → finalizer
      ├─ [ambiguous]    → END  (clarifying_questions surfaced to caller)
      └─ [unanswerable] → END  (failure_reason surfaced to caller)
"""

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, Field

from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm
from agent.llm import get_llm
from agent.state import AgentState
from agent.utils.redis_publisher import publish_node_event

logger = logging.getLogger(__name__)

from typing import Literal

def _resolve_ambiguity_type(parsed: dict) -> str:
    ambiguity_type = parsed.get("ambiguity_type")
    clarifying: str | None = parsed.get("clarifying_questions")

    # If the LLM generated a clarifying question, it MUST be ambiguous.
    if clarifying is not None and isinstance(clarifying, str) and clarifying.strip():
        return "ambiguous"

    if ambiguity_type in ["clear", "ambiguous", "unanswerable"]:
        return ambiguity_type

    return "clear"


class AmbiguityResult(BaseModel):
    reason: str = Field(default="", description="Explanation of the ambiguity detection decision. Always think through your reasoning here first.")
    ambiguity_type: Literal["clear", "ambiguous", "unanswerable"] = Field(
        description="The determined state of the query: 'clear' (proceed), 'ambiguous' (needs clarification), or 'unanswerable' (impossible)."
    )
    clarifying_questions: str | None = Field(default=None, description="Questions to ask the user if ambiguous. Null if clear or unanswerable.")


async def detect_ambiguity_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict:
    """
    Pre-SQL ambiguity gate — previously pre-SQL, now runs AFTER query_builder.

    Reads the jeen_catalog, the user query, the SQL attempt, and the SQL explanation,
    then asks an LLM whether the interpretation is deterministic
    or ambiguous/unanswerable.

    Returns a partial state update with:
      - ambiguity_result   : raw parsed JSON from the LLM
      - ambiguity_type     : "clear" | "ambiguous" | "unanswerable"
      - clarifying_questions / failure_reason (populated on non-clear paths)
    """
    thread_id = (
        config.get("configurable", {}).get("thread_id", "") if config else ""
    )
    await publish_node_event(thread_id, "detect_ambiguity")

    runtime_flags = state.get("runtime_flags") or {}

    # ── Gather inputs ─────────────────────────────────────────────────────────
    user_query: str = state.get("user_query") or ""
    # jeen_catalog now contains the catalog prompt
    current_time: str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Fetch system prompt from Langfuse ─────────────────────────────────────
    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_DETECT_AMBIGUITY)
    if langfuse_prompt is None:
        raise RuntimeError(
            f"Langfuse prompt '{settings.LANGFUSE_PROMPT_DETECT_AMBIGUITY}' could not be retrieved."
        )

    # The prompt template uses {{current_time}} — inject it now.
    raw_system = langfuse_prompt.prompt
    if isinstance(raw_system, list):
        # Chat-style prompt stored as list of role dicts; grab the system message.
        system_text = next(
            (m.get("content", "") for m in raw_system if m.get("role") == "system"),
            "",
        )
    else:
        system_text = str(raw_system)

    system_text = system_text.replace("{{current_time}}", current_time)

    # ── Build user message ────────────────────────────────────────────────────
    # NOTE: The field is labelled "Current Agent SQL Attempt" in the prompt to
    # stay consistent with the Langfuse prompt template. At this stage it
    # contains the sql_query from the query builder.
    user_message = (
        f"User Request: {state.get('user_query', '')}\n\n"
        f"Current Agent SQL Attempt:\n{state.get('sql_query', '')}\n\n"
        f"Agent's Explanation for SQL:\n{state.get('sql_explanation', '')}\n"
    )

    # ── LLM call ─────────────────────────────────────────────────────────────
    _llm = get_llm("detect_ambiguity", runtime_flags=runtime_flags)
    structured_llm = _llm.with_structured_output(AmbiguityResult, method="json_schema")
    messages = [SystemMessage(content=system_text), HumanMessage(content=user_message)]

    parsed: dict = {}
    ambiguity_type: str = "unanswerable"

    try:
        response = await structured_llm.ainvoke(messages)
        parsed = response.model_dump()
        ambiguity_type = _resolve_ambiguity_type(parsed)
    except Exception as exc:
        logger.error(f"Ambiguity detection structured output failed: {exc}")
        # Fallback to ambiguous on parsing failure to be safe
        parsed = {
            "ambiguity_type": "ambiguous",
            "reason": f"Ambiguity detection failed to parse LLM structured output: {exc}",
            "clarifying_questions": "Could you rephrase or add more detail to your request so we can interpret it precisely?",
        }
        ambiguity_type = "ambiguous"

    # ── Langfuse tracing ──────────────────────────────────────────────────────
    try:
        if langfuse_client.get_current_trace_id():
            langfuse_client.update_current_span(
                metadata={
                    "ambiguity_type": ambiguity_type,
                    "ambiguity_result": parsed,
                },
            )
    except Exception as exc:
        logger.warning("detect_ambiguity: Langfuse trace update failed: %s", exc)

    # ── Build state update ────────────────────────────────────────────────────
    update: dict = {
        "ambiguity_result": parsed,
        "ambiguity_type": ambiguity_type,
        "execution_path": ["detect_ambiguity"],
        # Always clear terminal fields; they are set below only on non-clear paths.
        "clarifying_questions": None,
        "failure_reason": None,
    }

    if ambiguity_type == "ambiguous":
        retry_count = state.get("ambiguity_retry_count") or 0
        if retry_count >= settings.MAX_AMBIGUITY_RETRIES:
            ambiguity_type = "unanswerable"
            update["ambiguity_type"] = ambiguity_type
            fr = "Max retries reached for ambiguous question. The agent was not able to resolve the ambiguity with the provided clarifications."
            update["failure_reason"] = fr
            update["escalation_reason"] = f"Unanswerable Query: {fr}"
            update["summary"] = f"**Unanswerable Request:**\n\n{fr}"
            return update

        cq = parsed.get("clarifying_questions") or parsed.get("reason") or ""
        update["clarifying_questions"] = cq
        update["escalation_reason"] = f"Ambiguity Detected: {cq}"
    elif ambiguity_type == "unanswerable":
        fr = parsed.get("reason") or parsed.get("clarifying_questions") or ""
        update["failure_reason"] = fr
        update["escalation_reason"] = f"Unanswerable Query: {fr}"
        update["summary"] = f"**Unanswerable Request:**\n\n{fr}"

    return update

# ── Ambiguity Resolution (HITL) ──────────────────────────────────────────────


async def ambiguity_resolution_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict:
    """
    HITL pause point for ambiguous queries.

    LangGraph interrupts BEFORE this node via interrupt_before=["ambiguity_resolution"].
    The graph pauses, the frontend surfaces state["clarifying_questions"] to the user,
    and the user submits a clarification via graph.update_state({"feedback": "<answer>"}).

    When resumed this node:
      1. Reads the user’s clarification from state["feedback"]
         (injected by update_state before resume).
      2. Increments ambiguity_retry_count so detect_ambiguity can enforce a loop guard.
      3. Resets ambiguity state fields so the retry gets a clean pass.
      4. Returns directly → schema_explorer (targeted retry, not a full extractor reset).

    If the user provides no feedback (empty string), the clarifying question is
    preserved in feedback so schema_explorer at minimum knows the context.
    """
    thread_id = (
        config.get("configurable", {}).get("thread_id", "") if config else ""
    )
    await publish_node_event(thread_id, "ambiguity_resolution")

    retry_count = (state.get("ambiguity_retry_count") or 0) + 1
    clarifying_questions = state.get("clarifying_questions") or ""
    user_feedback = state.get("feedback") or ""

    # If the human didn’t set feedback via update_state, carry the original
    # clarifying question forward so schema_explorer has context for its retry.
    if not user_feedback and clarifying_questions:
        user_feedback = f"[Previous ambiguity question] {clarifying_questions}"

    try:
        if langfuse_client.get_current_trace_id():
            langfuse_client.update_current_span(
                metadata={
                    "ambiguity_retry_count": retry_count,
                    "user_clarification": user_feedback,
                }
            )
    except Exception:
        pass

    new_query = state.get("user_query", "")
    if user_feedback and not user_feedback.startswith("[Previous"):
        new_query += f"\n[User Clarification: {user_feedback}]"

    return {
        # Update the main query so all subsequent nodes see the unified intent.
        "user_query": new_query,
        # Clear ambiguity decision so detect_ambiguity re-evaluates cleanly on retry.
        "ambiguity_type": None,
        "ambiguity_result": None,
        "clarifying_questions": None,
        # Clear feedback so it doesn't accidentally trigger the rejection_router later.
        "feedback": None,
        "ambiguity_retry_count": retry_count,
        "execution_path": ["ambiguity_resolution"],
    }
