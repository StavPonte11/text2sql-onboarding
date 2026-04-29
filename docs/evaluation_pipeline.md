# Evaluation Pipeline — Dataset Lifecycle & Scoring

## Overview

The evaluation system replaces all mock scoring with a real, deterministic pipeline. Every evaluation run is tied to a dataset version, executed against Trino, scored using the 3-layer scoring mechanism, and fully traced in Langfuse.

---

## Dataset Schema

### `datasets`

```sql
id              UUID PRIMARY KEY
table_id        UUID REFERENCES tables(id)
name            VARCHAR
version         INTEGER DEFAULT 1
source          ENUM('golden', 'user_feedback', 'generated')
status          ENUM('draft', 'active', 'archived')
question_count  INTEGER
coverage_simple   FLOAT   -- fraction of questions by type
coverage_complex  FLOAT
coverage_join     FLOAT
coverage_geo      FLOAT
coverage_aggregate FLOAT
created_at      TIMESTAMP
created_by      VARCHAR
```

### `dataset_questions`

```sql
id                    UUID PRIMARY KEY
dataset_id            UUID REFERENCES datasets(id)
table_id              UUID REFERENCES tables(id)
question              TEXT
expected_sql          TEXT
expected_result_shape JSONB  -- {row_count_min, row_count_max, expected_columns[]}
difficulty            ENUM('simple', 'medium', 'complex')
question_type         ENUM('simple', 'join', 'geo', 'aggregate', 'time_series')
source                ENUM('golden', 'user_feedback', 'generated')
weight                FLOAT  -- 1.0=simple, 1.5=medium, 2.0=complex, 2.5=multi-table, 2.0=geo
version               INTEGER DEFAULT 1
is_active             BOOLEAN DEFAULT TRUE
created_at            TIMESTAMP
```

---

## Dataset Versioning Rules

- Every edit to a dataset question creates a **new version** of that question
- The old version is retained with `is_active=FALSE` and linked to historical eval runs
- A new **dataset version** is created when:
  - A question is added, edited, or removed
  - The dataset is promoted from `draft` → `active`
- Evaluation runs always reference the **exact dataset version** they used

---

## Dataset Sources

### 1. Golden Questions (manual)

Created by table owner in the UI. Minimum enforcement:
- ≥ 10 questions before sandbox eval is allowed
- At least 2 complex, 1 join, 1 geo (for geo-relevant tables)

### 2. User Feedback (automatic)

When a user rates a query 👍 and it produces correct results → auto-proposed for dataset inclusion.

When a user rates a query 👎 and provides a correction → auto-proposed as a negative example with corrected SQL.

Both require **table owner approval** before being added to the active dataset.

### 3. Generated Edge Cases (future / Phase 2)

An LLM generates synthetic hard cases targeting weak coverage areas. Requires human review before activation.

---

## Evaluation Pipeline

### Trigger Conditions

| Trigger | Scope |
|---|---|
| Table owner requests sandbox eval | Table's active dataset |
| Table owner requests publish | Self-eval + all production datasets (regression) |
| Automated nightly | All production tables (regression monitoring) |
| Post-enrichment update | Mini-eval (5 questions from latest dataset) |

---

### Pipeline Steps (per question)

```
dataset_question
      │
      ▼
[A] Invoke TextToSQL agent (sandbox instance — no production data)
    │ normalized_query → discovery → compose → refine → execute
      │
      ▼
[B] Capture execution result
    │ result_rows, result_columns, execution_time_ms, refiner_iterations
      │
      ▼
[C] Compare result shape
    │ actual_row_count vs expected [min, max]
    │ actual_columns vs expected_columns
      │
      ▼
[D] Call LLM Judge
    │ inputs: question, expected_sql, generated_sql, result_metadata, schema
    │ outputs: 4 dimension scores + failure_type
      │
      ▼
[E] Apply scoring (3-layer)
    │ hard_gates → base_score → penalties → final_score
      │
      ▼
[F] Classify failure type (if failed)
    │ wrong_table | wrong_join | wrong_filter | hallucination | execution_error | empty_result_bug
      │
      ▼
[G] Write eval_results record
[H] Log to Langfuse (one trace per question, linked to eval run)
```

---

## Scoring Formula (from scoring_mechanism.md)

### Layer 1 — Hard Gates

| Condition | Effect |
|---|---|
| SQL execution failure | `final_score = 0.0` |
| Unauthorized table access | `final_score = 0.0` |
| Non-existent column referenced | `score ≤ 0.3` |
| Completely wrong table | `score ≤ 0.2` |

### Layer 2 — Core Dimensions

```python
base_score = (
    0.45 * result_correctness +
    0.20 * table_selection_correctness +
    0.15 * sql_semantic_equivalence +
    0.10 * result_shape_accuracy
)
```

Where:
- `result_correctness` = weighted average of row_match (0.4), column_match (0.3), value_match (0.3)
- `result_shape_accuracy` = 1.0 if row_count in [min, max] and all expected_columns present

### Layer 3 — Penalties

```python
hallucination_penalty = 0.30 if hallucination_detected else 0.0
refinement_penalty    = 0.05 * max(0, refiner_iterations - 1)   # no penalty for 1 retry
latency_penalty       = 0.05 if execution_time_ms > 30_000 else 0.0

final_score = max(0.0, base_score - hallucination_penalty - refinement_penalty - latency_penalty)
```

### Failure Type Score Caps

```python
FAILURE_SCORE_CAPS = {
    "wrong_table":     0.20,
    "wrong_join":      0.40,
    "wrong_filter":    0.60,
    "hallucination":   0.30,
    "execution_error": 0.00,
}
final_score = min(final_score, FAILURE_SCORE_CAPS.get(failure_type, 1.0))
```

### Question Weight

```python
QUESTION_WEIGHTS = {
    "simple":     1.0,
    "medium":     1.5,
    "complex":    2.0,
    "join":       2.5,
    "geo":        2.0,
    "aggregate":  1.5,
    "time_series": 1.5,
}
weight = QUESTION_WEIGHTS[question.question_type]
```

### Dataset Score

```python
dataset_score = sum(q.final_score * q.weight for q in questions) / sum(q.weight for q in questions)
```

---

## Thresholds

### Per Question

| Score | Decision |
|---|---|
| ≥ 0.85 | PASS |
| 0.60 – 0.84 | PARTIAL |
| < 0.60 | FAIL |

### Per Dataset

| Score | Decision |
|---|---|
| ≥ 0.90 | Production ready |
| 0.80 – 0.89 | Warning — review before publish |
| < 0.80 | BLOCKED — cannot publish |

---

## Regression System

### Triggered on every publish request:

1. **Self-eval** — run table's own active dataset → must score ≥ 0.90
2. **Production regression** — run ALL production tables' datasets with the new table included in discovery scope

### Blocking conditions:

```python
REGRESSION_BLOCK_DELTA   = 0.10   # score drop > 10% → HARD BLOCK
REGRESSION_WARNING_DELTA = 0.05   # score drop > 5%  → WARNING (platform can override)
```

If regression detected → show table owner:
- Which production questions were affected
- What the agent picked instead of the correct table
- Score before vs after

Platform team can manually override with written justification (logged in audit).

---

## Evaluation Report Structure

### Per Run

```json
{
  "run_id": "uuid",
  "dataset_id": "uuid",
  "dataset_version": 3,
  "table_id": "uuid",
  "overall_score": 0.87,
  "pass_rate": 0.82,
  "partial_rate": 0.10,
  "fail_rate": 0.08,
  "execution_success_rate": 0.95,
  "empty_result_rate": 0.03,
  "avg_refiner_iterations": 1.4,
  "avg_latency_ms": 3200,
  "failure_breakdown": {
    "wrong_table": 2,
    "wrong_join": 1,
    "wrong_filter": 4,
    "hallucination": 0,
    "execution_error": 1,
    "empty_result_bug": 0
  },
  "dimension_averages": {
    "table_selection_correctness": 0.93,
    "sql_semantic_equivalence": 0.81,
    "result_correctness": 0.86,
    "hallucination_rate": 0.0
  },
  "coverage": {
    "simple": {"count": 12, "pass_rate": 0.92},
    "complex": {"count": 5, "pass_rate": 0.74},
    "join": {"count": 3, "pass_rate": 0.67},
    "geo": {"count": 2, "pass_rate": 0.80}
  },
  "langfuse_dataset_run_id": "uuid"
}
```

### Coverage Gaps Detection

```python
COVERAGE_MINIMUMS = {
    "simple": 5,
    "complex": 2,
    "join": 1,
    "geo": 1,   # only if table is geo-relevant
}

gaps = [t for t, min_count in COVERAGE_MINIMUMS.items()
        if coverage[t]["count"] < min_count]
```

---

## Dataset Evolution (Automatic)

Failed questions → automatically proposed for dataset enrichment:

```python
if question.final_score < FAIL_THRESHOLD:
    DatasetEvolutionQueue.add({
        "source_question_id": question.id,
        "failure_type": question.failure_type,
        "generated_sql": question.generated_sql,
        "action": "review_and_add"  # requires table owner approval
    })
```

High-volume failure clusters → trigger system alert for prompt improvement review.
