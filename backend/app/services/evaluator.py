"""
evaluator.py — Mirrors the BaseLangfuseEvaluator / TextToSQLEvaluator class structure
from the main Text2SQL application, making the eventual app-merge straightforward.

Merge path:
  When integrating with the main app, replace TextToSQLEvaluator.task() with
  the real MCP client call from the main app's TextToSQLEvaluator:

      tool_result = await mcp_client.call_tool("text2sql", {
          "query": question.question,
          "databases": [schema_name],
      })
      return {"response": tool_result.data["response"]}

  Everything else (BaseLangfuseEvaluator, evaluators, run_single_dataset,
  run_all_datasets) can be shared as-is between both apps.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from langfuse.decorators import observe, langfuse_context
from sqlmodel import Session

from app.services.langfuse_client import langfuse_client as _lf_client, Evaluation
from app.services.scoring import (
    JudgeOutput, ExecutionResult, ExpectedShape,
    compute_score,
)
from app.models.models import GoldenQuestion, EvalResult
import logging

logger = logging.getLogger(__name__)


# ─── Abstract base — matches main app's BaseLangfuseEvaluator ─────────────────

class BaseLangfuseEvaluator(ABC):
    """
    Abstract base class for Langfuse-backed evaluators.

    Mirrors BaseLangfuseEvaluator from the main Text2SQL application so both
    apps can share the same evaluation interface after the merge.

    Subclasses must implement:
        task(item)          — evaluate one dataset item, return output dict
        get_evaluators()    — return list of scorer functions
    """

    def __init__(self, run_name: str):
        self.run_name = run_name
        self.lf = _lf_client

    # ── Must be implemented by subclasses ─────────────────────────────────────

    @abstractmethod
    @observe(name="eval-question")
    def task(self, item) -> Dict[str, Any]:
        """
        Evaluate a single Langfuse dataset item.

        MAIN APP SIGNATURE (real):
            tool_result = await mcp_client.call_tool("text2sql", {
                "query": item.input["query"],
                "databases": item.input["databases"],
            })
            return {"response": tool_result.data["response"]}

        Must return a dict that the evaluators can inspect.
        """
        ...

    @abstractmethod
    def get_evaluators(self) -> List:
        """Return list of evaluator callables: (item, result) -> Evaluation."""
        ...

    # ── Provided by base — identical to main app ───────────────────────────────

    def run_single_dataset(self, dataset_name: str):
        """
        Run evaluation on a single Langfuse dataset.
        Delegates to LangfuseDatasetService.run_experiment() which reliably
        iterates dataset items, calls task(), and scores each result.
        """
        if not self.lf.enabled:
            return None
        try:
            return self.lf.run_experiment(
                dataset_name=dataset_name,
                task=self.task,
                run_name=self.run_name,
                evaluators=self.get_evaluators(),
            )
        except Exception as e:
            logger.error(f"[Evaluator] run_single_dataset failed for '{dataset_name}': {e}", exc_info=True)
            return None

    def run_all_datasets(self, dataset_names: List[str]) -> Dict[str, Any]:
        """
        Run evaluation across multiple datasets (one per table).
        Returns mapping of dataset_name → ExperimentResult.
        Logs a summary of averages and status after all datasets complete.
        """
        results: Dict[str, Any] = {}
        for name in dataset_names:
            results[name] = self.run_single_dataset(name)

        # ── Summary log ───────────────────────────────────────────────
        q_scores = getattr(self, "question_scores", [])
        dim_totals = getattr(self, "dimension_totals", {})
        n = len(q_scores)
        if n > 0:
            avg_score = round(sum(s for s, _ in q_scores) / n, 3)
            dim_avgs = {k: round(v / n, 3) for k, v in dim_totals.items()}
            logger.info(
                f"[Evaluator] Summary — datasets: {list(dataset_names)}, "
                f"questions: {n}, avg_score: {avg_score}, "
                f"dim_avgs: {dim_avgs}"
            )
        else:
            logger.info(f"[Evaluator] Summary — no questions evaluated across {list(dataset_names)}")

        return results


# ─── Concrete implementation ───────────────────────────────────────────────────

class TextToSQLEvaluator(BaseLangfuseEvaluator):
    """
    Concrete evaluator for Text2SQL.

    Mirrors TextToSQLEvaluator from the main application.

    STUB:  task() currently uses simulated agent output.
    MERGE: Replace the body of task() with the real MCP client call
           from the main app's TextToSQLEvaluator when merging.
    """

    def __init__(
        self,
        run_name: str,
        session: Session,
        table_id: str,
        run_id: str,
        question_scores: List[tuple],
        failure_counts: Dict[str, int],
        dimension_totals: Optional[Dict[str, float]] = None,
    ):
        super().__init__(run_name=run_name)
        self.session = session
        self.table_id = table_id
        self.run_id = run_id
        # These lists/dicts are mutated by task() so the caller can aggregate results
        self.question_scores = question_scores
        self.failure_counts = failure_counts
        self.dimension_totals = dimension_totals if dimension_totals is not None else {}

    # ── Task ──────────────────────────────────────────────────────────────────

    @observe(name="eval-question")
    def task(self, item) -> Dict[str, Any]:
        """
        Evaluate a single question via the Text2SQL agent.

        ┌──────────────────────────────────────────────────────────┐
        │  STUB — replace with MCP client call from main app:      │
        │                                                          │
        │  tool_result = await mcp_client.call_tool("text2sql", {  │
        │      "query":     question_obj.question,                 │
        │      "databases": [question_obj.table_id],               │
        │  })                                                      │
        │  agent_response = tool_result.data["response"]           │
        │  # then build agent_result from agent_response           │
        └──────────────────────────────────────────────────────────┘

        Returns dict that evaluators and the caller can inspect.
        """
        trace_id = langfuse_context.get_current_trace_id()
        observation_id = langfuse_context.get_current_observation_id()

        q_id = item.metadata.get("question_id")
        logger.info(f"[Evaluator] Task starting for question_id: {q_id}")
        question_obj = self.session.get(GoldenQuestion, q_id)
        if not question_obj:
            logger.error(f"[Evaluator] Question {q_id} not found in database!")
            return {"trace_id": trace_id, "observation_id": observation_id}

        # Link this trace to the Langfuse dataset run
        self.lf.link_trace_to_dataset_run(
            dataset_item_id=item.id,
            trace_id=trace_id,
            observation_id=observation_id,
            run_name=self.run_name,
            run_metadata={"table_id": self.table_id},
        )

        # ── Input trace (matches real agent input schema) ──────────────────
        langfuse_context.update_current_trace(
            input={
                "query": question_obj.question,
                "databases": [question_obj.table_id],
            }
        )

        # ── Call agent (STUB — swap this block with MCP call on merge) ─────
        agent_result = self._call_agent_stub(question_obj.question)

        # ── Parse execution result ─────────────────────────────────────────
        exec_data = agent_result["execution"]
        execution = ExecutionResult(
            success=exec_data["success"],
            rows=exec_data["rows"],
            columns=exec_data["columns"],
            row_count=exec_data["row_count"],
            execution_time_ms=exec_data["execution_time_ms"],
            error_message=exec_data.get("error_message"),
        )

        # ── Call LLM judge (STUB) ──────────────────────────────────────────
        judge_raw = self._call_llm_judge_stub(
            question=question_obj.question,
            expected_sql=question_obj.expected_sql,
            generated_sql=agent_result["generated_sql"],
            execution_meta=exec_data,
        )
        judge = JudgeOutput(
            table_selection_correctness=judge_raw["table_selection_correctness"],
            sql_semantic_equivalence=judge_raw["sql_semantic_equivalence"],
            result_correctness=judge_raw["result_correctness"],
            hallucination_detected=judge_raw["hallucination_detected"],
            failure_type=judge_raw.get("failure_type"),
            reasoning=judge_raw.get("reasoning", {}),
            confidence_in_judgment=judge_raw.get("confidence_in_judgment", 0.8),
        )

        # ── Score ──────────────────────────────────────────────────────────
        breakdown = compute_score(
            execution=execution,
            expected_shape=ExpectedShape(row_count_min=0, row_count_max=999_999, expected_columns=[]),
            judge=judge,
            tables_used=agent_result["tables_used"],
            expected_tables=[],
            generated_columns=agent_result["generated_columns"],
            schema_columns=[],
            refiner_iterations=agent_result["refiner_iterations"],
            question_type=str(question_obj.question_type).lower(),
        )

        # ── Build result rows (real agent returns actual SQL rows) ─────────
        result_rows = [
            {
                "entityid": f"row-{i + 1}",
                "title": f"Result {i + 1}",
                "start_time": None,
                "content": f"stub row {i + 1}",
            }
            for i in range(min(3, exec_data["row_count"]))
        ]

        # ── Output trace ───────────────────────────────────────────────────
        langfuse_context.update_current_trace(
            output={"response": agent_result["generated_sql"]}
        )

        # ── Persist EvalResult ─────────────────────────────────────────────
        result_db = EvalResult(
            run_id=self.run_id,
            question_id=question_obj.id,
            score=breakdown.final_score,
            status="pass" if breakdown.question_status == "pass" else "fail",
            error_type=breakdown.failure_type,
        )
        self.session.add(result_db)

        # ── Accumulate stats ───────────────────────────────────────────────
        self.question_scores.append((breakdown.final_score, str(question_obj.question_type).lower()))
        if breakdown.failure_type and breakdown.failure_type in self.failure_counts:
            self.failure_counts[breakdown.failure_type] += 1
        
        for dim in ["table_selection_correctness", "sql_semantic_equivalence", "result_correctness"]:
            val = getattr(judge, dim, 0.0)
            self.dimension_totals[dim] = self.dimension_totals.get(dim, 0.0) + val

        logger.info(f"[Evaluator] Task completed for question {q_id} with score {breakdown.final_score}")

        return {
            "trace_id": trace_id,
            "observation_id": observation_id,
            "agent_result": agent_result,
            "breakdown": breakdown,
            "judge": judge,
            "execution": execution,
        }

    # ── Evaluators ────────────────────────────────────────────────────────────

    def get_evaluators(self) -> List:
        """Return the three standard evaluator functions."""
        return [
            self._judge_evaluator,
            self._execution_evaluator,
            self._final_score_evaluator,
        ]

    def _judge_evaluator(self, item, result) -> Evaluation:
        judge = result.get("judge")
        if not judge:
            return Evaluation(value=0.0, comment="No judge output")
        return Evaluation(
            value=judge.result_correctness,
            comment=f"Judge confidence: {judge.confidence_in_judgment}",
        )

    def _execution_evaluator(self, item, result) -> Evaluation:
        exec_data = result.get("execution")
        if not exec_data:
            return Evaluation(value=0.0, comment="No execution data")
        return Evaluation(
            value=1.0 if exec_data.success else 0.0,
            comment=exec_data.error_message,
        )

    def _final_score_evaluator(self, item, result) -> Evaluation:
        breakdown = result.get("breakdown")
        if not breakdown:
            return Evaluation(value=0.0, comment="No breakdown")
        return Evaluation(
            value=breakdown.final_score,
            comment=f"Status: {breakdown.question_status}",
        )

    # ── SQL execution (matches main app's _execute_sql_query) ─────────────────

    def _execute_sql_query(self, sql: str, schema_name: str) -> Dict[str, Any]:
        """
        Execute a SQL query against the configured data source.

        STUB — in the main app this runs against Trino via the MCP client.
        When merging: replace with the real Trino execution call.
        """
        # TODO: replace with real Trino call
        return {
            "success": True,
            "rows": [],
            "columns": [],
            "row_count": 0,
            "execution_time_ms": 0,
            "error_message": None,
        }

    # ── Internal stubs (removed on merge) ────────────────────────────────────

    @observe(as_type="generation")
    def _call_agent_stub(self, question: str) -> Dict[str, Any]:
        """
        STUB — simulates the Text2SQL agent response.

        MERGE: Delete this method. Replace the call in task() with:
            tool_result = mcp_client.call_tool("text2sql", {
                "query": question,
                "databases": [self.table_id],
            })
            return {"response": tool_result.data["response"], ...}
        """
        success = random.random() > 0.1
        row_count = random.randint(0, 5000) if success else 0
        iterations = random.choices([0, 1, 2, 3], weights=[60, 25, 10, 5])[0]
        return {
            "generated_sql": f"SELECT * FROM stub_table LIMIT 100",
            "tables_used": ["stub_table"],
            "generated_columns": ["id", "name", "value"],
            "refiner_iterations": iterations,
            "query_translation": f"[HE] {question[:40]}...",
            "hebrew_answer": "[HE] תשובה מדומה לפי תוצאות השאילתה.",
            "execution": {
                "success": success,
                "rows": [],
                "columns": ["id", "name", "value"] if success else [],
                "row_count": row_count,
                "execution_time_ms": random.randint(200, 8000),
                "error_message": None if success else "Stub execution error",
            },
        }

    @observe(as_type="generation")
    def _call_llm_judge_stub(
        self, question: str, expected_sql: str, generated_sql: str, execution_meta: dict
    ) -> Dict[str, Any]:
        """
        STUB — simulates the LLM judge response.

        MERGE: Delete this method. Replace the call in task() with the real
               judge call from the main app (OpenAI / Anthropic via Langfuse).
        """
        exec_success = execution_meta.get("success", False)
        base = random.uniform(0.70, 0.95) if exec_success else random.uniform(0.20, 0.45)
        return {
            "table_selection_correctness": round(min(1.0, base + random.uniform(-0.1, 0.1)), 3),
            "sql_semantic_equivalence":    round(min(1.0, base + random.uniform(-0.15, 0.1)), 3),
            "result_correctness":          round(min(1.0, base + random.uniform(-0.05, 0.1)), 3),
            "hallucination_detected":      random.random() < 0.05,
            "failure_type":                None,
            "reasoning": {
                "table_selection": "Stub reasoning",
                "sql_equivalence":  "Stub reasoning",
                "result_correctness": "Stub reasoning",
                "hallucination": "No hallucination detected in stub mode",
            },
            "confidence_in_judgment": round(random.uniform(0.7, 0.95), 3),
        }
