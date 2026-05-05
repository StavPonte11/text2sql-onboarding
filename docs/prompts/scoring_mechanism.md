You are implementing the **Scoring, Calibration, and Evaluation Decision System** for a production-grade TextToSQL platform.

This system is CRITICAL. It determines:

* whether tables are publishable
* whether evaluations pass
* whether the system is trustworthy

---

# 🎯 OBJECTIVE

Build a **robust, calibrated, and safe scoring system** that:

* prioritizes execution correctness
* avoids false positives
* supports regression detection
* enables reliable publish decisions

---

# 🧠 SCORING ARCHITECTURE

You MUST implement a **3-layer scoring system**:

---

## 🔹 LAYER 1 — HARD GATES (MANDATORY)

These override all scoring.

If triggered:

* SQL execution failure → score = 0
* Unauthorized table usage → score = 0
* Non-existent table/column → score ≤ 0.3
* Completely wrong table → score ≤ 0.2

---

## 🔹 LAYER 2 — CORE DIMENSIONS

Each dimension ∈ [0,1]

### Dimensions:

1. result_correctness → weight = 0.45
2. table_selection_accuracy → weight = 0.20
3. sql_semantic_equivalence → weight = 0.15
4. result_shape_accuracy → weight = 0.10

---

## 🔹 LAYER 3 — PENALTIES

Penalties subtract from base score:

* hallucination_penalty → up to 0.3
* refinement_iteration_penalty
* latency_penalty

---

## 🧮 FINAL SCORE

```python id="score_calc"
base_score =
    0.45 * result_correctness +
    0.20 * table_selection_accuracy +
    0.15 * sql_semantic_equivalence +
    0.10 * result_shape_accuracy

final_score = max(0, base_score - total_penalties)
```

---

# 📊 RESULT CORRECTNESS (PRIMARY SIGNAL)

Implement 3 modes:

---

## Mode 1 — Exact Comparison

* row count match
* column match
* value similarity

```python id="res_exact"
score =
    0.4 * row_match +
    0.3 * column_match +
    0.3 * value_match
```

---

## Mode 2 — Approximate Comparison

* allow tolerance (aggregations)

---

## Mode 3 — Fallback

* use LLM judge + shape validation

---

# 🤖 LLM-AS-A-JUDGE (CONTROLLED USAGE)

Judge MUST return:

* table_selection_score
* sql_equivalence_score
* reasoning_score
* hallucination_flag

---

## RULES

* Judge CANNOT override execution failure
* Judge is secondary signal only
* Must be calibrated using known test set

---

# 📉 FAILURE TAXONOMY

Classify each result into:

* wrong_table
* wrong_join
* wrong_filter
* hallucination
* execution_error
* empty_result_bug

---

## Apply score caps:

| failure_type    | max_score |
| --------------- | --------- |
| wrong_table     | 0.2       |
| wrong_join      | 0.4       |
| wrong_filter    | 0.6       |
| hallucination   | 0.3       |
| execution_error | 0         |

---

# 📊 DATASET SCORING

Each question has weight:

* simple → 1.0
* medium → 1.5
* complex → 2.0
* multi-table → 2.5
* geo → 2.0

---

## Dataset Score:

```python id="dataset_calc"
dataset_score = weighted_average(question_scores)
```

---

# 🚨 THRESHOLDS

## Per Question

* ≥ 0.85 → PASS
* 0.6–0.85 → PARTIAL
* < 0.6 → FAIL

---

## Per Dataset

* ≥ 0.90 → production ready
* 0.80–0.90 → warning
* < 0.80 → block

---

# 🔁 REGRESSION RULES

Before publish:

* run all production datasets

---

## Blocking Conditions:

* score drop > 0.10 → BLOCK
* score drop > 0.05 → WARNING

---

# 🧠 CONFIDENCE SCORING (RUNTIME)

Separate from evaluation score.

---

## Formula:

```python id="confidence_calc"
confidence =
    0.3 * retrieval_score +
    0.2 * schema_match +
    0.2 * execution_success +
    0.2 * historical_success +
    0.1 * validation_checks
```

---

## RULE:

* NEVER show high confidence for low evaluation accuracy

---

# 📊 COVERAGE TRACKING

Track dataset coverage:

* simple queries
* joins
* aggregations
* geo queries

---

## Output:

coverage_score ∈ [0,1]

---

# 🚨 ANTI-GAMING RULES

You MUST detect:

* SQL memorization
* overfitting to dataset
* missing filters (SELECT * abuse)

---

# ⚙️ DEFAULT CONFIG

```python id="defaults_config"
PASS_THRESHOLD = 0.85
BLOCK_THRESHOLD = 0.80
REGRESSION_BLOCK = 0.10
MAX_ITERATIONS = 4
MAX_LATENCY_SEC = 120
```

---

# 📁 OUTPUT REQUIREMENTS

Generate:

1. Python scoring module:

   * compute_score()
   * apply_gates()
   * classify_failure()
2. Evaluation aggregator
3. Dataset scoring logic
4. Confidence scoring module
5. Example scoring outputs
6. Integration points with evaluation pipeline

---

# ⚠️ CRITICAL REQUIREMENTS

* Execution correctness MUST dominate
* System MUST be calibrated (not arbitrary)
* No reliance on LLM alone
* Must be deterministic and reproducible

---

# 🧠 FINAL GOAL

Build a scoring system that:

* prevents bad tables from being published
* correctly identifies weak areas
* aligns with real-world correctness
* provides reliable trust signals to users
