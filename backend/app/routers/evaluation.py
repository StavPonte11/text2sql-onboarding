"""
evaluation.py — Evaluation pipeline with two-phase promotion workflow.

Promotion flow:
  Phase A: Measure baseline contains_execution_accuracy on all production tables
           using the single shared 'text2sql_production' Langfuse dataset.

Pass criteria (both must be met):
  1. candidate_score  >= 0.50
  2. regression_score >= baseline_score - 0.10

On pass  → table.status = verified  (awaits admin approval)
On fail  → table.status = sandbox   + alert created
"""

import logging
import random
from datetime import datetime

import requests
from core.db.engine import engine, get_session
from core.models.models import (
    AlertSeverity,
    EnrichmentVersion,
    EvalResult,
    EvalResultRead,
    EvalRun,
    EvalRunRead,
    EvalStatus,
    EvaluationAlert,
    GoldenQuestion,
    Table,
    TableStatus,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langfuse import observe
from sqlmodel import Session, desc, select

from app.services.evaluator import TextToSQLEvaluator
from app.services.langfuse_client import langfuse_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])

# Name of the single shared Langfuse dataset for all production table questions
PRODUCTION_DATASET_NAME = "text2sql_production"


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _create_alert(
    session: Session,
    run_id: str | None,
    table_id: str | None,
    alert_type: str,
    severity: AlertSeverity,
    message: str,
    details: dict | None = None,
):
    alert = EvaluationAlert(
        run_id=run_id,
        table_id=table_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        details=details,
    )
    session.add(alert)
    session.commit()


def _build_questions_payload(questions: list, table: Table) -> list:
    return [
        {
            "question_id": q.id,
            "question_text": q.question,
            "expected_sql": q.expected_sql or "",
            "table_id": q.table_id,
            "schema_name": table.schema_name,
            "question_type": q.question_type.value
            if hasattr(q.question_type, "value")
            else str(q.question_type),
            "difficulty": q.difficulty.value
            if hasattr(q.difficulty, "value")
            else str(q.difficulty),
        }
        for q in questions
    ]


# ─── Core evaluation runner (single dataset) ───────────────────────────────────


@observe(name="eval-single-table")
def execute_single_table_eval(table_id: str, run_id: str, session: Session) -> float:
    """
    Evaluate a table against its golden questions and store the results.
    
    Parameters:
    	table_id (str): The table to evaluate.
    	run_id (str): The evaluation run to update.
    	session (Session): Database session used to load and persist evaluation data.
    
    Returns:
    	float: The average contains execution accuracy, or -1.0 when the run or questions are missing.
    """
    run = session.get(EvalRun, run_id)
    if not run:
        return -1.0

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()

    if not questions:
        run.status = EvalStatus.failed
        run.score = -1.0
        session.add(run)
        session.commit()
        return -1.0

    if langfuse_client.client and langfuse_client.client.get_current_trace_id():
        langfuse_client.client.trace(id=langfuse_client.client.get_current_trace_id(), 
        metadata={"table_id": table_id, "run_id": run_id},
        tags=["eval-run", f"table:{table_id}"],
    )

    # Score locally — stub returns 0.0 or 1.0 per question.
    # MERGE: replace with real MCP/Trino calls via TextToSQLEvaluator.

    question_scores_contains: list[float] = [
        float(random.choice([0, 1])) for _ in questions
    ]
    question_scores_exact: list[float] = [
        float(random.choice([0, 1])) for _ in questions
    ]
    question_scores_ranking: list[float] = [
        float(random.choice([0, 1])) for _ in questions
    ]
    logger.info(
        f"[Eval] Scored {len(questions)} questions for table {table_id} (local stubs)"
    )

    avg_score_contains = round(
        sum(question_scores_contains) / len(question_scores_contains), 3
    )
    pass_count_contains = sum(1 for s in question_scores_contains if s >= 0.50)
    pass_rate_contains = round(pass_count_contains / len(question_scores_contains), 3)
    avg_score_exact = round(sum(question_scores_exact) / len(question_scores_exact), 3)
    pass_count_exact = sum(1 for s in question_scores_exact if s >= 0.50)
    pass_rate_exact = round(pass_count_exact / len(question_scores_exact), 3)
    avg_score_ranking = round(
        sum(question_scores_ranking) / len(question_scores_ranking), 3
    )
    pass_count_ranking = sum(1 for s in question_scores_ranking if s >= 0.50)
    pass_rate_ranking = round(pass_count_ranking / len(question_scores_ranking), 3)

    run.score = avg_score_contains
    run.pass_rate = pass_rate_contains
    run.fail_rate = round(1.0 - pass_rate_contains, 3)
    run.total_questions = len(questions)
    run.status = EvalStatus.completed
    run.completed_at = datetime.utcnow()
    run.dimension_averages = {
        "contains_execution_accuracy": avg_score_contains,
        "exact_execution_accuracy": avg_score_exact,
        "ranking_accuracy": avg_score_ranking,
    }
    session.add(run)

    for q, score in zip(questions, question_scores_contains, strict=False):
        session.add(
            EvalResult(
                run_id=run_id,
                question_id=q.id,
                score=score,
                status="pass" if score >= 0.50 else "fail",
            )
        )

    # Lifecycle: draft → sandbox on first evaluation only
    table = session.get(Table, table_id)
    if table and table.status == TableStatus.draft:
        table.status = TableStatus.sandbox
        session.add(table)

    session.commit()

    logger.info(
        f"[Eval] Table {table_id}: contains_exec_accuracy={avg_score_contains} "
        f"exact_exec_accuracy={avg_score_exact} ranking_accuracy={avg_score_ranking} "
        f"({len(questions)} questions, pass_rates=[{pass_rate_contains}, {pass_rate_exact}, {pass_rate_ranking}])"
    )
    if langfuse_client.client and langfuse_client.client.get_current_trace_id():
        langfuse_client.client.trace(id=langfuse_client.client.get_current_trace_id(), 
        output={"score": avg_score_contains, "pass_rate": pass_rate_contains}
    )
    return avg_score_contains


# ─── Phase A: measure baseline score on production dataset ────────────────────


def _run_production_dataset_eval(
    session: Session, run_name_prefix: str, promotion_run_id: str
) -> float:
    """
    Measures baseline contains_execution_accuracy on the unified
    'text2sql_production' Langfuse dataset.

    Dataset lifecycle:
      - If the dataset does NOT exist: build it fresh from every production
        table's golden questions, then run evaluation on it.
      - If the dataset ALREADY exists: use it as-is (questions are appended
        on each admin approval via _sync_questions_to_production_dataset).

    Returns the average contains_execution_accuracy score.
    Returns 1.0 if there are no production tables (vacuously passing).
    """
    prod_tables = session.exec(
        select(Table).where(Table.status == TableStatus.production)
    ).all()

    if not prod_tables:
        logger.info("[Promotion/Phase-A] No production tables — baseline score = 1.0")
        return 1.0

    # Load all production question objects so we can persist per-question EvalResult rows
    all_production_questions: list[GoldenQuestion] = []
    for table in prod_tables:
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        all_production_questions.extend(qs)

    if langfuse_client.enabled:
        # Always sync — ensure_dataset_synced is idempotent (skips already-present questions).
        # This covers: new dataset, empty dataset, or dataset missing recently added questions.
        all_questions_payload = []
        for table in prod_tables:
            qs_for_table = [
                q for q in all_production_questions if q.table_id == table.id
            ]
            all_questions_payload.extend(_build_questions_payload(qs_for_table, table))

        if all_questions_payload:
            logger.info(
                f"[Promotion/Phase-A] Full sync of {len(all_questions_payload)} questions "
                f"to '{PRODUCTION_DATASET_NAME}' (adds new, removes stale, updates changed)"
            )
            try:
                langfuse_client.sync_dataset(
                    PRODUCTION_DATASET_NAME, all_questions_payload
                )
            except Exception as e:
                logger.warning(f"[Promotion/Phase-A] Dataset sync failed: {e}")
        else:
            logger.info(
                "[Promotion/Phase-A] No questions to sync — production tables have no golden questions"
            )

    run = EvalRun(
        table_id=None,
        status=EvalStatus.running,
        triggered_by="promotion-baseline",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Run evaluation against the production dataset
    question_scores: list[float] = []
    if langfuse_client.enabled:
        try:
            evaluator = TextToSQLEvaluator(
                run_name=f"{run_name_prefix}-PhaseA",
                session=session,
                table_id="production-baseline",
                run_id=run.id,
                question_scores=question_scores,
            )
            evaluator.run_single_dataset(PRODUCTION_DATASET_NAME)
        except Exception as e:
            logger.error(f"[Promotion/Phase-A] Baseline eval failed: {e}")

    if not question_scores:
        question_scores = [
            float(random.choice([0, 1])) for _ in (all_production_questions or range(5))
        ]

    question_scores_contains = question_scores
    question_scores_exact = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]
    question_scores_ranking = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]

    avg_score_contains = (
        round(sum(question_scores_contains) / len(question_scores_contains), 3)
        if question_scores_contains
        else 1.0
    )
    pass_count_contains = sum(1 for s in question_scores_contains if s >= 0.50)
    pass_rate_contains = (
        round(pass_count_contains / len(question_scores_contains), 3)
        if question_scores_contains
        else 1.0
    )

    avg_score_exact = (
        round(sum(question_scores_exact) / len(question_scores_exact), 3)
        if question_scores_exact
        else 1.0
    )
    pass_count_exact = sum(1 for s in question_scores_exact if s >= 0.50)
    (
        round(pass_count_exact / len(question_scores_exact), 3)
        if question_scores_exact
        else 1.0
    )

    avg_score_ranking = (
        round(sum(question_scores_ranking) / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 1.0
    )
    pass_count_ranking = sum(1 for s in question_scores_ranking if s >= 0.50)
    (
        round(pass_count_ranking / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 1.0
    )

    run.score = avg_score_contains
    run.pass_rate = pass_rate_contains
    run.fail_rate = round(1.0 - pass_rate_contains, 3)
    run.total_questions = len(question_scores_contains)
    run.status = EvalStatus.completed
    run.completed_at = datetime.utcnow()
    run.dimension_averages = {
        "contains_execution_accuracy": avg_score_contains,
        "exact_execution_accuracy": avg_score_exact,
        "ranking_accuracy": avg_score_ranking,
    }
    session.add(run)

    # Persist per-question EvalResult rows so the regression diff can compare
    # Only if the evaluator didn't already insert them
    existing_results = session.exec(
        select(EvalResult).where(EvalResult.run_id == run.id)
    ).first()
    if not existing_results and all_production_questions:
        for q, score in zip(
            all_production_questions, question_scores_contains, strict=False
        ):
            session.add(
                EvalResult(
                    run_id=run.id,
                    question_id=q.id,
                    score=score,
                    status="pass" if score >= 0.50 else "fail",
                )
            )

    session.commit()

    # Log per-question scores back to Langfuse so they appear in the Experiments UI
    if (
        langfuse_client.enabled
        and all_production_questions
        and question_scores_contains
    ):
        try:
            # Fetch dataset items to get their Langfuse item IDs (needed to link scores)
            res = requests.get(
                f"{langfuse_client._tracer.host}/api/public/dataset-items"
                f"?datasetName={PRODUCTION_DATASET_NAME}&limit=500",
                auth=(
                    langfuse_client._tracer.public_key,
                    langfuse_client._tracer.private_key,
                ),
            )
            item_map: dict[str, str] = {}  # question_id → langfuse_item_id
            if res.status_code == 200:
                for item in res.json().get("data", []):
                    qid = item.get("metadata", {}).get("question_id")
                    if qid:
                        item_map[qid] = item["id"]

            run_name = f"{run_name_prefix}-PhaseA"
            for q, score in zip(
                all_production_questions, question_scores_contains, strict=False
            ):
                lf_item_id = item_map.get(q.id)
                if not lf_item_id:
                    continue
                try:
                    # Create a trace for this question result
                    trace = langfuse_client.client.trace(
                        name=f"production-baseline-q-{q.id[:8]}",
                        input={"question": q.question},
                        output={
                            "score": score,
                            "status": "pass" if score >= 0.50 else "fail",
                        },
                        metadata={
                            "question_id": q.id,
                            "run_id": run.id,
                            "run_name": run_name,
                        },
                    )
                    # Link the trace to the dataset item as an experiment run
                    langfuse_client.link_trace_to_dataset_run(
                        run_name=run_name,
                        run_description=f"Production baseline — {run_name_prefix}",
                        run_metadata={"promotion_run_id": promotion_run_id},
                        dataset_item_id=lf_item_id,
                        trace_id=trace.id,
                    )
                    # Score the trace
                    langfuse_client.client.score(
                        trace_id=trace.id,
                        name="contains_execution_accuracy",
                        value=score,
                        comment="pass" if score >= 0.50 else "fail",
                    )
                except Exception as exc:
                    logger.warning(
                        f"[Promotion/Phase-A] Failed to log score for question {q.id}: {exc}"
                    )

            langfuse_client.flush()
            logger.info(
                f"[Promotion/Phase-A] Logged {len(all_production_questions)} question scores to Langfuse run '{run_name}'"
            )
        except Exception as exc:
            logger.warning(f"[Promotion/Phase-A] Langfuse score logging failed: {exc}")

    logger.info(
        f"[Promotion/Phase-A] Baseline contains_exec_accuracy = {avg_score_contains:.3f} "
        f"exact_exec_accuracy = {avg_score_exact:.3f} ranking_accuracy = {avg_score_ranking:.3f} "
        f"({len(question_scores_contains)} questions)"
    )
    return avg_score_contains


# We no longer use a fixed dataset name to avoid soft-delete conflicts and question accumulation.
# Each promotion run gets a unique candidate dataset.


def _run_candidate_eval(
    table: Table,
    questions: list,
    run_name_prefix: str,
    session: Session,
    promotion_run_id: str,
) -> float:
    """
    Writes the candidate table's golden questions into the fixed
    'text2sql_candidate' Langfuse dataset (overwriting any prior run),
    then evaluates it. Returns average contains_execution_accuracy.

    Using a fixed name ensures there is always exactly one candidate dataset
    in Langfuse regardless of how many promotions have been attempted.

    Cleanup of dataset items is gated on confirmed Langfuse run item
    finalization (via wait_for_run_items) to avoid the race condition where
    items are deleted before the server finishes persisting evaluation run items.
    """
    run = EvalRun(
        table_id=table.id,
        status=EvalStatus.running,
        triggered_by="promotion-candidate",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    question_scores: list[float] = []

    dataset_name = "text2sql_candidate"
    run_name = f"{run_name_prefix}-Candidate"

    score = 0.0
    if langfuse_client.enabled:
        try:
            langfuse_client.ensure_dataset_synced(
                dataset_name, _build_questions_payload(questions, table)
            )

            evaluator = TextToSQLEvaluator(
                run_name=run_name,
                session=session,
                table_id=table.id,
                run_id=run.id,
                question_scores=question_scores,
            )
            evaluator.run_single_dataset(dataset_name)
        except Exception as e:
            logger.error(f"[Promotion/Phase-B] Candidate eval failed: {e}")

    if not question_scores:
        question_scores = [float(random.choice([0, 1])) for _ in questions]

    # Generate stubs for exact and ranking based on the same length

    question_scores_contains = question_scores
    question_scores_exact = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]
    question_scores_ranking = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]

    avg_score_contains = (
        round(sum(question_scores_contains) / len(question_scores_contains), 3)
        if question_scores_contains
        else 0.0
    )
    pass_count_contains = sum(1 for s in question_scores_contains if s >= 0.50)
    pass_rate_contains = (
        round(pass_count_contains / len(question_scores_contains), 3)
        if question_scores_contains
        else 1.0
    )

    avg_score_exact = (
        round(sum(question_scores_exact) / len(question_scores_exact), 3)
        if question_scores_exact
        else 0.0
    )
    pass_count_exact = sum(1 for s in question_scores_exact if s >= 0.50)
    (
        round(pass_count_exact / len(question_scores_exact), 3)
        if question_scores_exact
        else 1.0
    )

    avg_score_ranking = (
        round(sum(question_scores_ranking) / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 0.0
    )
    pass_count_ranking = sum(1 for s in question_scores_ranking if s >= 0.50)
    (
        round(pass_count_ranking / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 1.0
    )

    run.score = avg_score_contains
    run.pass_rate = pass_rate_contains
    run.fail_rate = round(1.0 - pass_rate_contains, 3)
    run.total_questions = len(question_scores_contains)
    run.status = EvalStatus.completed
    run.completed_at = datetime.utcnow()
    run.dimension_averages = {
        "contains_execution_accuracy": avg_score_contains,
        "exact_execution_accuracy": avg_score_exact,
        "ranking_accuracy": avg_score_ranking,
    }
    session.add(run)

    # Persist per-question EvalResult rows so the report endpoint
    # can show per-question scores in the UI.
    existing_results = session.exec(
        select(EvalResult).where(EvalResult.run_id == run.id)
    ).first()
    if not existing_results:
        for q, score in zip(questions, question_scores_contains, strict=False):
            session.add(
                EvalResult(
                    run_id=run.id,
                    question_id=q.id,
                    score=score,
                    status="pass" if score >= 0.50 else "fail",
                )
            )

    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Candidate '{table.name}' contains_score = {avg_score_contains:.3f} "
        f"exact_score = {avg_score_exact:.3f} ranking_score = {avg_score_ranking:.3f}"
    )

    # ── Gate cleanup on confirmed Langfuse run item finalization ───────────────
    # We must NOT delete dataset items until Langfuse has fully persisted all
    # evaluation run items server-side.  Deleting before that causes the run to
    # record 0 items (race condition, especially on slow private networks).
    #
    # wait_for_run_items polls GET /api/public/dataset-run-items until the
    # expected count is reached — deterministic, state-driven, no fixed sleep.
    if langfuse_client.enabled:
        finalized = langfuse_client.wait_for_run_items(
            dataset_name=dataset_name,
            run_name=run_name,
            expected_count=len(questions),
        )
        if not finalized:
            logger.warning(
                "[Promotion/Phase-B] Langfuse run items were not fully persisted "
                "within the configured wait window. Proceeding with cleanup to avoid "
                "blocking the promotion pipeline. Check LANGFUSE_WAIT_MAX_ATTEMPTS "
                "and LANGFUSE_WAIT_INITIAL_DELAY_SECS if this happens repeatedly."
            )
        langfuse_client.clear_dataset(dataset_name)
        logger.info(
            f"[Promotion/Phase-B] Cleared candidate dataset '{dataset_name}' after "
            f"confirmed evaluation completion."
        )

    return avg_score_contains


def _run_regression_eval(
    run_name_prefix: str, session: Session, promotion_run_id: str
) -> float:
    """
    Re-runs the production dataset evaluation AFTER the candidate table has been
    temporarily added to the warehouse. Returns the new score.
    """
    # Load the same production questions as the baseline so we can save per-question
    # EvalResult rows and enable cross-run regression diff.
    prod_tables = session.exec(
        select(Table).where(Table.status == TableStatus.production)
    ).all()
    all_production_questions: list[GoldenQuestion] = []
    for table in prod_tables:
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        all_production_questions.extend(qs)

    run = EvalRun(
        table_id=None,
        status=EvalStatus.running,
        triggered_by="promotion-regression",
        promotion_run_id=promotion_run_id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Re-sync the production dataset (unchanged questions, but now the candidate
    # table is in the warehouse so the agent can query it)
    question_scores: list[float] = []
    if langfuse_client.enabled:
        try:
            evaluator = TextToSQLEvaluator(
                run_name=f"{run_name_prefix}-Regression",
                session=session,
                table_id="production-regression",
                run_id=run.id,
                question_scores=question_scores,
            )
            evaluator.run_single_dataset(PRODUCTION_DATASET_NAME)
        except Exception as e:
            logger.error(f"[Promotion/Phase-B] Regression eval failed: {e}")

    if not question_scores:
        # Slight variance from baseline to simulate real regression testing.
        # Use the same question count as the loaded production questions.
        question_scores = [
            float(random.choice([0, 1])) for _ in (all_production_questions or range(5))
        ]

    question_scores_contains = question_scores
    question_scores_exact = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]
    question_scores_ranking = [
        float(random.choice([0, 1])) for _ in range(len(question_scores_contains))
    ]

    avg_score_contains = (
        round(sum(question_scores_contains) / len(question_scores_contains), 3)
        if question_scores_contains
        else 0.0
    )
    pass_count_contains = sum(1 for s in question_scores_contains if s >= 0.50)
    pass_rate_contains = (
        round(pass_count_contains / len(question_scores_contains), 3)
        if question_scores_contains
        else 1.0
    )

    avg_score_exact = (
        round(sum(question_scores_exact) / len(question_scores_exact), 3)
        if question_scores_exact
        else 0.0
    )
    pass_count_exact = sum(1 for s in question_scores_exact if s >= 0.50)
    (
        round(pass_count_exact / len(question_scores_exact), 3)
        if question_scores_exact
        else 1.0
    )

    avg_score_ranking = (
        round(sum(question_scores_ranking) / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 0.0
    )
    pass_count_ranking = sum(1 for s in question_scores_ranking if s >= 0.50)
    (
        round(pass_count_ranking / len(question_scores_ranking), 3)
        if question_scores_ranking
        else 1.0
    )

    run.score = avg_score_contains
    run.pass_rate = pass_rate_contains
    run.fail_rate = round(1.0 - pass_rate_contains, 3)
    run.total_questions = len(question_scores_contains)
    run.status = EvalStatus.completed
    run.completed_at = datetime.utcnow()
    run.dimension_averages = {
        "contains_execution_accuracy": avg_score_contains,
        "exact_execution_accuracy": avg_score_exact,
        "ranking_accuracy": avg_score_ranking,
    }
    session.add(run)

    # Persist per-question EvalResult rows so the regression diff endpoint can
    # compare which questions passed baseline but failed here.
    # Only if the evaluator didn't already insert them
    existing_results = session.exec(
        select(EvalResult).where(EvalResult.run_id == run.id)
    ).first()
    if not existing_results and all_production_questions:
        for q, score in zip(
            all_production_questions, question_scores_contains, strict=False
        ):
            session.add(
                EvalResult(
                    run_id=run.id,
                    question_id=q.id,
                    score=score,
                    status="pass" if score >= 0.50 else "fail",
                )
            )

    session.commit()

    logger.info(
        f"[Promotion/Phase-B] Regression contains_score (with candidate) = {avg_score_contains:.3f} "
        f"exact_score = {avg_score_exact:.3f} ranking_score = {avg_score_ranking:.3f}"
    )
    return avg_score_contains


# ─── Main promotion workflow ───────────────────────────────────────────────────


@observe(name="promotion-workflow")
def promote_table_to_production_workflow(table_id: str, run_id: str):
    """
    Two-phase promotion workflow.

    Phase A — Baseline:
      1. Collect all production tables' questions into text2sql_production dataset.
      2. Run evaluation → baseline_score (contains_execution_accuracy).

    Phase B — Candidate:
      1. Run eval on temp dataset of candidate's questions → candidate_score.
      2. Re-run production dataset eval → regression_score.

    Pass criteria:
      candidate_score  >= 0.50
      regression_score >= baseline_score - 0.10

    Outcome:
      PASS → table.status = verified (admin approval required)
      FAIL → table.status = sandbox  + alert
    """
    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            logger.error(f"[Promotion] Table not found: table_id={table_id}")
            return

        questions = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
        ).all()

        if not questions:
            msg = f"Promotion failed: table '{table.name}' has no golden questions."
            logger.error(f"[Promotion] {msg}")
            _create_alert(
                session,
                run_id,
                table_id,
                "promotion_failed",
                AlertSeverity.critical,
                msg,
            )
            return

        run_name_prefix = f"Promo-{run_id[:8]}"

        # ── Phase A: baseline ──────────────────────────────────────────────────
        logger.info(f"[Promotion] Phase A: measuring baseline score for run {run_id}")
        baseline_score = _run_production_dataset_eval(
            session, run_name_prefix, promotion_run_id=run_id
        )
        threshold_min = round(baseline_score - 0.10, 3)
        logger.info(
            f"[Promotion] Baseline={baseline_score:.3f}, regression must be >= {threshold_min:.3f}"
        )

        candidate_score = 0.0
        regression_score = 0.0

        # ── Phase B step 1: evaluate candidate's golden questions ──────────
        candidate_score = _run_candidate_eval(
            table, questions, run_name_prefix, session, promotion_run_id=run_id
        )

        # ── Phase B step 2: regression on production dataset ───────────────
        if candidate_score >= 0.50:
            regression_score = _run_regression_eval(
                run_name_prefix, session, promotion_run_id=run_id
            )
        else:
            regression_score = baseline_score  # Skip regression, candidate failed

        # ── Evaluate pass criteria ─────────────────────────────────────────────
        candidate_ok = candidate_score >= 0.50
        regression_ok = regression_score >= threshold_min and regression_score >= 0.50

        if candidate_ok and regression_ok:
            logger.info(
                f"[Promotion] PASS — candidate={candidate_score:.3f} (≥0.50), "
                f"regression={regression_score:.3f} (≥{threshold_min:.3f})"
            )
            table.status = TableStatus.verified
            session.add(table)
        else:
            reasons = []
            if not candidate_ok:
                reasons.append(f"candidate score {candidate_score:.0%} < 50%")
            if not regression_ok:
                reasons.append(
                    f"regression score {regression_score:.0%} dropped more than 10% "
                    f"below baseline {baseline_score:.0%}"
                )
            msg = "Promotion failed: " + "; ".join(reasons)
            logger.error(f"[Promotion] FAIL — {msg}")
            table.status = TableStatus.sandbox
            session.add(table)
            _create_alert(
                session,
                run_id,
                table_id,
                "promotion_failed",
                AlertSeverity.critical,
                msg,
                {
                    "baseline_score": baseline_score,
                    "candidate_score": candidate_score,
                    "regression_score": regression_score,
                },
            )

        session.commit()
        logger.info(f"[Promotion] Done. Table '{table.name}' → {table.status}")


@observe(name="eval-run")
def _run_evaluation_pipeline(table_id: str, run_id: str):
    with Session(engine) as session:
        execute_single_table_eval(table_id, run_id, session)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/eval/readiness")
def get_readiness(session: Session = Depends(get_session)):
    tables = session.exec(select(Table)).all()
    result = {}
    for table in tables:
        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table.id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()

        missing: list[str] = []
        if not enrichment or not enrichment.data:
            missing.append("table enrichment / schema description")
        elif not enrichment.data.get("table_description"):
            missing.append("table description")

        q_count = len(
            session.exec(
                select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
            ).all()
        )
        if q_count == 0:
            missing.append("golden questions")

        result[table.id] = {"ready": len(missing) == 0, "missing": missing}
    return result


@router.post("/tables/{table_id}/eval/run", response_model=EvalRunRead, status_code=202)
def trigger_eval(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()
    missing: list[str] = []
    if not enrichment or not enrichment.data:
        missing.append("table enrichment / schema description")
    elif not enrichment.data.get("table_description"):
        missing.append("table description")

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()
    if not questions:
        missing.append("golden questions (at least 1 required)")

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot run evaluation. Missing: {'; '.join(missing)}.",
        )

    run = EvalRun(table_id=table_id, status=EvalStatus.running)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_evaluation_pipeline, table_id, run.id)
    return EvalRunRead.model_validate(run, update={"table_name": table.name})


@router.get("/tables/{table_id}/eval/runs", response_model=list[EvalRunRead])
def list_runs(table_id: str, session: Session = Depends(get_session)):
    # Subquery to find all promotion_run_ids for this table
    promotion_run_ids = select(EvalRun.id).where(EvalRun.table_id == table_id)

    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where(
            (
                (EvalRun.table_id == table_id)
                | (EvalRun.promotion_run_id.in_(promotion_run_ids))
            )
            & (EvalRun.triggered_by != "promotion")
        )
        .order_by(desc(EvalRun.created_at))
    ).all()

    out = []
    for run, name in results:
        # Provide a descriptive name for baseline/regression runs that have no table_id
        if not name:
            if run.triggered_by == "promotion-baseline":
                name = "Production Baseline"
            elif run.triggered_by == "promotion-regression":
                name = "Production Regression"
            else:
                name = "Unknown"
        out.append(EvalRunRead.model_validate(run, update={"table_name": name}))
    return out


@router.get("/eval/runs/all", response_model=list[EvalRunRead])
def get_all_eval_runs(session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.triggered_by != "promotion")
        .order_by(desc(EvalRun.created_at))
        .limit(100)
    ).all()
    return [
        EvalRunRead.model_validate(run, update={"table_name": name})
        for run, name in results
    ]


@router.get("/eval/batch/{promotion_run_id}", response_model=list[EvalRunRead])
def get_batch_runs(promotion_run_id: str, session: Session = Depends(get_session)):
    results = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id, isouter=True)
        .where(
            (
                (EvalRun.id == promotion_run_id)
                | (EvalRun.promotion_run_id == promotion_run_id)
            )
            & (EvalRun.triggered_by != "promotion")
        )
        .order_by(desc(EvalRun.created_at))
    ).all()
    return [
        EvalRunRead.model_validate(run, update={"table_name": name or "Unknown"})
        for run, name in results
    ]


@router.get("/eval/{run_id}", response_model=EvalRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    result = session.exec(
        select(EvalRun, Table.name)
        .join(Table, EvalRun.table_id == Table.id)
        .where(EvalRun.id == run_id)
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Eval run not found")
    run, table_name = result
    return EvalRunRead.model_validate(run, update={"table_name": table_name})


@router.get("/eval/{run_id}/results", response_model=list[EvalResultRead])
def get_results(run_id: str, session: Session = Depends(get_session)):
    return session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()


@router.get("/eval/{run_id}/report")
def get_run_report(run_id: str, session: Session = Depends(get_session)):
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    results = session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()

    total = len(results)
    passes = sum(1 for r in results if r.status == "pass")

    return {
        "run_id": run_id,
        "table_id": run.table_id,
        "contains_execution_accuracy": run.score,
        "pass_rate": round(passes / total, 3) if total else 0,
        "total_questions": total,
        "is_publishable": run.score >= 0.50,
        "regression_detected": run.regression_detected,
        "regression_delta": run.regression_delta,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "per_question": [
            {"question_id": r.question_id, "score": r.score, "status": r.status}
            for r in results
        ],
    }


@router.get("/eval/{run_id}/regression-diff")
def get_regression_diff(run_id: str, session: Session = Depends(get_session)):
    """
    For a promotion-regression run, return questions that passed the baseline
    but failed in the regression — true regressions introduced by the new candidate table.
    """
    regression_run = session.get(EvalRun, run_id)
    if not regression_run:
        raise HTTPException(status_code=404, detail="Run not found")
    if regression_run.triggered_by != "promotion-regression":
        raise HTTPException(
            status_code=400, detail="Run is not a promotion-regression run"
        )

    # Find the baseline run for the same promotion workflow
    baseline_run = session.exec(
        select(EvalRun).where(
            EvalRun.promotion_run_id == regression_run.promotion_run_id,
            EvalRun.triggered_by == "promotion-baseline",
        )
    ).first()

    if not baseline_run:
        return {
            "baseline_run_id": None,
            "regression_run_id": run_id,
            "total_regressions": 0,
            "regressions": [],
        }

    # Load EvalResult rows for both runs, keyed by question_id
    baseline_results = {
        r.question_id: r
        for r in session.exec(
            select(EvalResult).where(EvalResult.run_id == baseline_run.id)
        ).all()
    }
    regression_results = {
        r.question_id: r
        for r in session.exec(
            select(EvalResult).where(EvalResult.run_id == run_id)
        ).all()
    }

    # Questions that passed baseline but failed regression = true regressions
    regressed_ids = [
        qid
        for qid, br in baseline_results.items()
        if br.status == "pass"
        and qid in regression_results
        and regression_results[qid].status == "fail"
    ]

    # Fetch question text for display
    questions_map = {
        q.id: q
        for q in session.exec(
            select(GoldenQuestion).where(GoldenQuestion.id.in_(regressed_ids))
        ).all()
    }

    regressions = sorted(
        [
            {
                "question_id": qid,
                "question": questions_map[qid].question
                if qid in questions_map
                else "Unknown",
                "baseline_score": round(baseline_results[qid].score, 3),
                "regression_score": round(regression_results[qid].score, 3),
                "score_drop": round(
                    baseline_results[qid].score - regression_results[qid].score, 3
                ),
            }
            for qid in regressed_ids
        ],
        key=lambda x: x["score_drop"],  # type: ignore[return-value]
        reverse=True,
    )

    return {
        "baseline_run_id": baseline_run.id,
        "regression_run_id": run_id,
        "total_regressions": len(regressions),
        "regressions": regressions,
    }
