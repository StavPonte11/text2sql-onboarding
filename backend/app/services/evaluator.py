"""
evaluator.py — TextToSQL evaluation engine.

Three standard evaluators:
  1. exact_match                  — generated SQL equals expected SQL exactly
  2. exact_execution_accuracy     — executed result rows match expected rows exactly
  3. contains_execution_accuracy  — expected rows are a subset of the returned rows
                                    (PRIMARY METRIC used for promotion decisions)

The task() method calls the TextToSQL MCP tool and returns the generated SQL.
The _execute_sql_query() method calls the Trino MCP tool to execute the SQL.

Merge path:
  Replace the MCP tool call stubs in task() and _execute_sql_query() with the
  real MCP client calls from the main Text2SQL application.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import Any

from langfuse.decorators import langfuse_context, observe
from sqlmodel import Session

from app.models.models import EvalResult, GoldenQuestion
from app.services.langfuse_client import Evaluation, langfuse_client as _lf_client

logger = logging.getLogger(__name__)


# ─── Abstract base — keep for merge compatibility with main app ────────────────


class BaseLangfuseEvaluator(ABC):
    """
    Abstract base class for Langfuse-backed evaluators.
    Mirrors BaseLangfuseEvaluator from the main Text2SQL application.
    """

    def __init__(self, run_name: str):
        self.run_name = run_name
        self.lf = _lf_client

    @abstractmethod
    @observe(name="eval-question")
    def task(self, item) -> dict[str, Any]:
        """Evaluate a single Langfuse dataset item. Return dict with 'response' key."""
        ...

    @abstractmethod
    def get_all_evaluations(self) -> list:
        """Return the list of all evaluator callables: (item, result) -> Evaluation."""
        ...

    def run_single_dataset(self, dataset_name: str):
        if not self.lf.enabled:
            return None
        try:
            return self.lf.run_experiment(
                dataset_name=dataset_name,
                task=self.task,
                run_name=self.run_name,
                evaluators=self.get_all_evaluations(),
            )
        except Exception as e:
            logger.error(
                f"[Evaluator] run_single_dataset failed for '{dataset_name}': {e}",
                exc_info=True,
            )
            return None


# ─── Concrete implementation ────────────────────────────────────────────────────


class TextToSQLEvaluator(BaseLangfuseEvaluator):
    """
    Evaluator for Text2SQL that uses 3 standard Langfuse metrics.

    The primary metric is contains_execution_accuracy.
    Scores are accumulated in question_scores for external aggregation.
    """

    def __init__(
        self,
        run_name: str,
        session: Session,
        table_id: str,
        run_id: str,
        question_scores: list[
            float
        ],  # list of contains_execution_accuracy scores (0.0-1.0)
    ):
        super().__init__(run_name=run_name)
        self.session = session
        self.table_id = table_id
        self.run_id = run_id
        self.question_scores = question_scores  # mutated by task()

    # ── Task ───────────────────────────────────────────────────────────────────

    @observe(name="eval-question")
    def task(self, item) -> dict[str, Any]:
        """
        Evaluate a single question via the Text2SQL MCP tool.

        ┌──────────────────────────────────────────────────────────────────────┐
        │  MERGE — replace this block with the real MCP client call:           │
        │                                                                      │
        │  tool_response = mcp_client.call_tool("TextToSQL", {                 │
        │      "query":     item.input["query"],                               │
        │      "databases": item.input["databases"],                           │
        │  })                                                                  │
        │  generated_sql = tool_response.data["result"]                        │
        └──────────────────────────────────────────────────────────────────────┘

        Returns:
            dict with keys: response (generated SQL), question_id, expected_sql
        """
        trace_id = langfuse_context.get_current_trace_id()
        observation_id = langfuse_context.get_current_observation_id()

        q_id = item.metadata.get("question_id")
        question_obj = self.session.get(GoldenQuestion, q_id)
        if not question_obj:
            logger.error(f"[Evaluator] Question {q_id} not found in DB")
            return {
                "trace_id": trace_id,
                "observation_id": observation_id,
                "response": None,
            }

        # Link trace to Langfuse dataset run
        self.lf.link_trace_to_dataset_run(
            dataset_item_id=item.id,
            trace_id=trace_id,
            observation_id=observation_id,
            run_name=self.run_name,
            run_metadata={"table_id": self.table_id},
        )

        langfuse_context.update_current_trace(
            input={
                "query": question_obj.question,
                "databases": [question_obj.table_id],
            },
        )

        # ── STUB: call TextToSQL MCP tool ──────────────────────────────────────
        # MERGE: replace with real call:
        #   tool_response = mcp_client.call_tool("TextToSQL", {
        #       "query": question_obj.question,
        #       "databases": [question_obj.table_id],
        #   })
        #   generated_sql = tool_response.data["result"]
        generated_sql = f"SELECT * FROM {question_obj.table_id} LIMIT 100"  # STUB

        langfuse_context.update_current_trace(output={"response": generated_sql})

        # Persist EvalResult (score will be updated by evaluators after task returns)
        result_db = EvalResult(
            run_id=self.run_id,
            question_id=question_obj.id,
            score=0.0,  # updated below after evaluators run
            status="pending",
            error_type=None,
        )
        self.session.add(result_db)
        self.session.flush()  # get the ID without full commit

        return {
            "trace_id": trace_id,
            "observation_id": observation_id,
            "response": generated_sql,
            "question_id": q_id,
            "expected_sql": question_obj.expected_sql,
            "result_db_id": result_db.id,
        }

    # ── The 3 evaluators ───────────────────────────────────────────────────────

    def exact_match(self, item, result) -> Evaluation:
        """
        Evaluator 1: Exact Match
        Score = 1.0 if generated SQL matches expected SQL exactly (case-insensitive,
        whitespace-normalised), 0.0 otherwise.

        STUB: Returns 0.0 or 1.0 until the real agent is integrated.
        """
        # ── STUB ───────────────────────────────────────────────────────────────
        value = float(random.choice([0, 1]))
        # ── REAL (uncomment on merge) ───────────────────────────────────────────
        # generated = (result.get("response") or "").strip().lower()
        # expected  = (result.get("expected_sql") or "").strip().lower()
        # import re
        # normalize = lambda s: re.sub(r"\s+", " ", s)
        # value = 1.0 if normalize(generated) == normalize(expected) else 0.0
        return Evaluation(value=value, comment="exact_match (stub)")

    def exact_execution_accuracy(self, item, result) -> Evaluation:
        """
        Evaluator 2: Exact Execution Accuracy
        Executes the generated SQL and checks whether the result rows exactly match
        the expected rows (order-independent set comparison).

        STUB: Returns 0.0 or 1.0.
        """
        # ── STUB ───────────────────────────────────────────────────────────────
        value = float(random.choice([0, 1]))
        # ── REAL (uncomment on merge) ───────────────────────────────────────────
        # generated_sql = result.get("response")
        # if not generated_sql:
        #     return Evaluation(value=0.0, comment="no SQL generated")
        # exec_result = self._execute_sql_query(generated_sql, item.input.get("databases", [""])[0])
        # expected_rows = item.expected_output.get("rows", [])
        # actual_rows = exec_result.get("rows", [])
        # match = set(map(tuple, sorted(map(sorted, actual_rows)))) == \
        #         set(map(tuple, sorted(map(sorted, expected_rows))))
        # value = 1.0 if match else 0.0
        return Evaluation(value=value, comment="exact_execution_accuracy (stub)")

    def contains_execution_accuracy(self, item, result) -> Evaluation:
        """
        Evaluator 3: Contains Execution Accuracy  ← PRIMARY METRIC
        Executes the generated SQL and checks whether every expected result row is
        contained within the returned rows (subset check).

        STUB: Returns a random value ≥ 0.35.
        The accumulation into self.question_scores happens here so the promotion
        workflow can read it after all questions are evaluated.
        """
        # ── STUB ───────────────────────────────────────────────────────────────
        value = random.randint(0, 1)
        # ── REAL (uncomment on merge) ───────────────────────────────────────────
        # generated_sql = result.get("response")
        # if not generated_sql:
        #     self.question_scores.append(0.0)
        #     return Evaluation(value=0.0, comment="no SQL generated")
        # exec_result = self._execute_sql_query(generated_sql, item.input.get("databases", [""])[0])
        # expected_rows = set(map(tuple, item.expected_output.get("rows", [])))
        # actual_rows   = set(map(tuple, exec_result.get("rows", [])))
        # if not expected_rows:
        #     value = 1.0   # nothing expected → trivially satisfied
        # else:
        #     value = 1.0 if expected_rows.issubset(actual_rows) else 0.0

        self.question_scores.append(value)

        # Update EvalResult in DB
        if result and "result_db_id" in result:
            result_db = self.session.get(EvalResult, result["result_db_id"])
            if result_db:
                result_db.score = value
                result_db.status = "pass" if value >= 0.50 else "fail"
                self.session.add(result_db)
                self.session.commit()

        return Evaluation(
            value=value, comment="contains_execution_accuracy (stub — primary metric)"
        )

    def get_all_evaluations(self) -> list:
        """Return the 3 standard evaluator functions in order."""
        return [
            self.exact_match,
            self.exact_execution_accuracy,
            self.contains_execution_accuracy,  # PRIMARY — must be last so scores are accumulated
        ]

    # ── SQL execution via Trino MCP tool ───────────────────────────────────────

    def _execute_sql_query(self, sql: str, schema_name: str) -> dict[str, Any]:
        """
        Execute a SQL query via the Trino MCP tool.

        ┌──────────────────────────────────────────────────────────────────────┐
        │  MERGE — replace this block with the real Trino MCP call:            │
        │                                                                      │
        │  tool_response = mcp_client.call_tool("Trino", {"query": sql})       │
        │  return tool_response.data                                           │
        └──────────────────────────────────────────────────────────────────────┘

        Returns a dict with keys: success, rows, columns, row_count, error_message
        """
        # STUB
        logger.debug(f"[Evaluator] _execute_sql_query STUB for schema={schema_name}")
        return {
            "success": True,
            "rows": [],
            "columns": [],
            "row_count": 0,
            "error_message": None,
        }
