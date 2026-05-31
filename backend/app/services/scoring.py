"""
scoring.py — 3-layer deterministic scoring system.

Layer 1: Hard gates (override everything)
Layer 2: Core dimension scores (weighted sum)
Layer 3: Penalties (subtract from base)

Reference: docs/prompts/scoring_mechanism.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langfuse.decorators import observe

# ─── Config ───────────────────────────────────────────────────────────────────

PASS_THRESHOLD = 0.85
PARTIAL_THRESHOLD = 0.60
BLOCK_THRESHOLD = 0.50  # dataset-level
REGRESSION_BLOCK = 0.10
REGRESSION_WARN = 0.05
MAX_ITERATIONS = 4

QUESTION_WEIGHTS = {
    "simple": 1.0,
    "medium": 1.5,
    "complex": 2.0,
    "join": 2.5,
    "geo": 2.0,
    "aggregate": 1.5,
    "time_series": 1.5,
}

FAILURE_SCORE_CAPS = {
    "wrong_table": 0.20,
    "wrong_join": 0.40,
    "wrong_filter": 0.60,
    "hallucination": 0.30,
    "execution_error": 0.00,
    "empty_result_bug": 0.50,
}

FAILURE_TYPES = [
    "wrong_table",
    "wrong_join",
    "wrong_filter",
    "hallucination",
    "execution_error",
    "empty_result_bug",
    "partial_correct",
]


# ─── Dimension inputs ──────────────────────────────────────────────────────────


@dataclass
class JudgeOutput:
    """Structured output from the LLM-as-Judge."""

    table_selection_correctness: float = 0.0  # 0-1
    sql_semantic_equivalence: float = 0.0  # 0-1
    result_correctness: float = 0.0  # 0-1
    hallucination_detected: bool = False
    failure_type: str | None = None
    reasoning: dict = field(default_factory=dict)
    confidence_in_judgment: float = 0.8


@dataclass
class ExecutionResult:
    """Raw output from Trino execution."""

    success: bool
    rows: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    error_message: str | None = None


@dataclass
class ExpectedShape:
    """Expected result shape defined in the dataset question."""

    row_count_min: int = 0
    row_count_max: int = 999_999
    expected_columns: list[str] = field(default_factory=list)


@dataclass
class ScoreBreakdown:
    # Hard gate
    hard_gate_triggered: bool = False
    hard_gate_reason: str | None = None

    # Core dimensions
    result_correctness: float = 0.0
    table_selection_correctness: float = 0.0
    sql_semantic_equivalence: float = 0.0
    result_shape_accuracy: float = 0.0

    # Penalties
    hallucination_penalty: float = 0.0
    refinement_penalty: float = 0.0
    latency_penalty: float = 0.0

    # Result
    base_score: float = 0.0
    total_penalties: float = 0.0
    final_score: float = 0.0
    question_status: str = "fail"  # pass | partial | fail
    failure_type: str | None = None


# ─── Layer 1: Hard Gates ───────────────────────────────────────────────────────


def apply_hard_gates(
    execution: ExecutionResult,
    tables_used: list[str],
    expected_tables: list[str],
    generated_columns: list[str],
    schema_columns: list[str],
) -> tuple[bool, str | None, float]:
    """
    Returns: (gate_triggered, reason, capped_score)
    """
    if not execution.success:
        return True, "sql_execution_failure", 0.0

    # Check for non-existent columns in generated SQL
    if schema_columns:
        invalid_cols = [c for c in generated_columns if c not in schema_columns]
        if invalid_cols:
            return True, f"non_existent_columns: {invalid_cols}", 0.3

    # Check for completely wrong primary table
    if expected_tables and tables_used:
        primary_expected = expected_tables[0] if expected_tables else None
        primary_used = tables_used[0] if tables_used else None
        if (
            primary_expected
            and primary_used
            and primary_expected.lower() != primary_used.lower()
        ):
            # Allow if it's a known alias or the table name is a substring
            if primary_expected.lower() not in primary_used.lower():
                return True, "wrong_primary_table", 0.2

    return False, None, 1.0  # no gate triggered


# ─── Layer 2: Core Dimension Scoring ──────────────────────────────────────────


def score_result_shape(execution: ExecutionResult, expected: ExpectedShape) -> float:
    """
    Score: 1.0 if row count in range AND all expected columns present.
    """
    if not execution.success:
        return 0.0

    row_match = (
        1.0
        if expected.row_count_min <= execution.row_count <= expected.row_count_max
        else 0.3
    )

    if expected.expected_columns:
        returned = set(c.lower() for c in execution.columns)
        expected_set = set(c.lower() for c in expected.expected_columns)
        col_overlap = (
            len(returned & expected_set) / len(expected_set) if expected_set else 1.0
        )
    else:
        col_overlap = 1.0

    return round(0.5 * row_match + 0.5 * col_overlap, 3)


def score_result_correctness_from_shape(
    execution: ExecutionResult, expected: ExpectedShape
) -> float:
    """
    Deterministic result correctness when exact result comparison is not possible.
    Uses shape + zero-result penalty as proxy.
    """
    if not execution.success:
        return 0.0

    if execution.row_count == 0 and expected.row_count_min > 0:
        return 0.0  # empty result when non-empty expected → zero score

    shape_score = score_result_shape(execution, expected)
    return round(shape_score, 3)


# ─── Layer 3: Penalties ────────────────────────────────────────────────────────


def compute_penalties(
    hallucination_detected: bool,
    refiner_iterations: int,
    execution_time_ms: int,
) -> dict[str, float]:
    hallucination_penalty = 0.30 if hallucination_detected else 0.0
    refinement_penalty = 0.05 * max(0, refiner_iterations - 1)  # no penalty for 1 retry
    latency_penalty = 0.05 if execution_time_ms > 30_000 else 0.0
    return {
        "hallucination_penalty": hallucination_penalty,
        "refinement_penalty": min(refinement_penalty, 0.15),  # cap at 0.15
        "latency_penalty": latency_penalty,
    }


# ─── Failure Classification ────────────────────────────────────────────────────


def classify_failure(
    execution: ExecutionResult,
    judge: JudgeOutput,
    expected: ExpectedShape,
) -> str | None:
    if not execution.success:
        return "execution_error"
    if execution.row_count == 0 and expected.row_count_min > 0:
        return "empty_result_bug"
    if judge.hallucination_detected:
        return "hallucination"
    if judge.failure_type and judge.failure_type in FAILURE_TYPES:
        return judge.failure_type

    # Infer from dimension scores
    if judge.table_selection_correctness < 0.3:
        return "wrong_table"
    if (
        judge.sql_semantic_equivalence < 0.4
        and judge.table_selection_correctness >= 0.7
    ):
        return "wrong_filter"
    if judge.result_correctness < 0.6:
        return "partial_correct"

    return None


# ─── Main Scoring Function ─────────────────────────────────────────────────────


@observe()
def compute_score(
    execution: ExecutionResult,
    expected_shape: ExpectedShape,
    judge: JudgeOutput,
    tables_used: list[str],
    expected_tables: list[str],
    generated_columns: list[str],
    schema_columns: list[str],
    refiner_iterations: int = 0,
    question_type: str = "simple",
) -> ScoreBreakdown:
    bd = ScoreBreakdown()

    # --- Layer 1: Hard Gates ---
    gate_triggered, gate_reason, gate_cap = apply_hard_gates(
        execution, tables_used, expected_tables, generated_columns, schema_columns
    )
    if gate_triggered:
        bd.hard_gate_triggered = True
        bd.hard_gate_reason = gate_reason
        bd.final_score = gate_cap
        bd.failure_type = classify_failure(execution, judge, expected_shape)
        bd.question_status = "fail"
        return bd

    # --- Layer 2: Core Dimensions ---
    result_correctness = score_result_correctness_from_shape(execution, expected_shape)
    result_shape = score_result_shape(execution, expected_shape)

    base_score = (
        0.45 * result_correctness
        + 0.20 * judge.table_selection_correctness
        + 0.15 * judge.sql_semantic_equivalence
        + 0.10 * result_shape
        + 0.10 * judge.result_correctness  # was missing — weights now sum to 1.0
    )

    bd.result_correctness = round(result_correctness, 3)
    bd.table_selection_correctness = round(judge.table_selection_correctness, 3)
    bd.sql_semantic_equivalence = round(judge.sql_semantic_equivalence, 3)
    bd.result_shape_accuracy = round(result_shape, 3)
    bd.base_score = round(base_score, 3)

    # --- Layer 3: Penalties ---
    penalties = compute_penalties(
        judge.hallucination_detected,
        refiner_iterations,
        execution.execution_time_ms,
    )
    bd.hallucination_penalty = penalties["hallucination_penalty"]
    bd.refinement_penalty = penalties["refinement_penalty"]
    bd.latency_penalty = penalties["latency_penalty"]
    bd.total_penalties = sum(penalties.values())

    raw_score = max(0.0, base_score - bd.total_penalties)

    # Apply failure type score cap
    failure_type = classify_failure(execution, judge, expected_shape)
    bd.failure_type = failure_type
    if failure_type and failure_type in FAILURE_SCORE_CAPS:
        raw_score = min(raw_score, FAILURE_SCORE_CAPS[failure_type])

    bd.final_score = round(raw_score, 3)

    # Status
    if bd.final_score >= PASS_THRESHOLD:
        bd.question_status = "pass"
    elif bd.final_score >= PARTIAL_THRESHOLD:
        bd.question_status = "partial"
    else:
        bd.question_status = "fail"

    return bd


# ─── Dataset Aggregation ───────────────────────────────────────────────────────


@observe()
def compute_dataset_score(question_scores: list[tuple[float, str]]) -> dict:
    """
    Args:
        question_scores: list of (final_score, question_type)
    Returns:
        dict with dataset_score, pass_rate, fail_rate, is_publishable, breakdown
    """
    if not question_scores:
        return {"dataset_score": 0.0, "is_publishable": False}

    weighted_sum = 0.0
    total_weight = 0.0
    passes = partials = fails = 0

    for score, qtype in question_scores:
        weight = QUESTION_WEIGHTS.get(qtype, 1.0)
        weighted_sum += score * weight
        total_weight += weight
        if score >= PASS_THRESHOLD:
            passes += 1
        elif score >= PARTIAL_THRESHOLD:
            partials += 1
        else:
            fails += 1

    dataset_score = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0
    total = len(question_scores)

    return {
        "dataset_score": dataset_score,
        "pass_rate": round(passes / total, 3),
        "partial_rate": round(partials / total, 3),
        "fail_rate": round(fails / total, 3),
        "is_publishable": dataset_score >= BLOCK_THRESHOLD,
        "total_questions": total,
        "total_weight": round(total_weight, 2),
    }


# ─── Confidence Scoring (Runtime) ─────────────────────────────────────────────


def compute_confidence_score(
    retrieval_confidence: float,
    schema_match_score: float,
    execution_success: bool,
    historical_success_rate: float | None,
    validation_checks_passed: float,
) -> dict:
    """
    Runtime confidence score — separate from evaluation score.
    Hard rule: if execution failed → 0.0
    """
    if not execution_success:
        return {"confidence_score": 0.0, "breakdown": {"hard_gate": "execution_failed"}}

    hist = historical_success_rate if historical_success_rate is not None else 0.5

    score = (
        0.30 * retrieval_confidence
        + 0.20 * schema_match_score
        + 0.20 * float(execution_success)
        + 0.20 * hist
        + 0.10 * validation_checks_passed
    )
    score = round(min(1.0, max(0.0, score)), 3)

    return {
        "confidence_score": score,
        "breakdown": {
            "retrieval_confidence": round(retrieval_confidence, 3),
            "schema_match": round(schema_match_score, 3),
            "execution_success": 1.0,
            "historical_success_rate": round(hist, 3),
            "validation_checks": round(validation_checks_passed, 3),
        },
    }
