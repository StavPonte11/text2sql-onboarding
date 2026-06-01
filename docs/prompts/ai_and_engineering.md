You are designing the AI and engineering systems for a production-grade TextToSQL platform inside "The Agency".

This system is NOT a prototype. It must support:

* Real evaluation
* Continuous improvement
* Observability
* Dataset lifecycle
* Trust and explainability

---

# 🎯 OBJECTIVE

Build a **robust, self-improving, data-aware TextToSQL system** that is:

* Accurate
* Measurable
* Interpretable
* Governed
* Continuously improving via feedback and evaluation

---

# 🧠 SYSTEM ARCHITECTURE

End-to-end pipeline:

1. Query Normalization
2. Table Discovery (hybrid retrieval)
3. Context Builder (schema + enrichment + profiling)
4. SQL Composer
5. Execution (Trino)
6. Refiner Loop (max bounded)
7. Result Validator
8. Confidence Scorer (NEW)
9. Insight & Warning Generator (NEW)

All steps MUST emit traces to Langfuse.

---

# 📊 PROFILING-AWARE CONTEXT (REQUIRED)

Use profiling data to enrich prompts:

* Column statistics (min/max, cardinality)
* Top categorical values
* Null ratios
* Join hints
* Distribution awareness

This must:

* Reduce hallucinations
* Improve WHERE clause accuracy
* Improve join selection

---

# 🧠 DATASET SYSTEM (CRITICAL)

Golden questions are NOT just tests.

You must implement a full dataset lifecycle:

---

## Dataset Creation

Each table has a dataset built from:

* Golden questions
* Real user queries (from audit + feedback)
* Generated edge cases (optional advanced)

Dataset schema:

* id
* table_id
* question
* expected_sql
* expected_result_shape
* metadata:

  * difficulty
  * query_type (simple, join, geo, aggregation)
  * source (golden / user / generated)

---

## Dataset Versioning

* Every change creates a new dataset version
* Evaluations are always tied to a dataset version

---

# 🧪 REAL EVALUATION SYSTEM (REPLACING MOCKS)

You MUST implement real evaluations using:

* Actual agent execution
* Actual Trino queries
* Real result comparison

---

## Evaluation Pipeline

For each question:

1. Run agent → generate SQL
2. Execute SQL in Trino
3. Compare:

   * expected SQL (semantic match)
   * result shape
   * result correctness
4. Run LLM-as-judge
5. Aggregate scores

---

# 🤖 LLM-AS-A-JUDGE (MANDATORY)

You must design a structured judge system.

---

## Judge Inputs:

* user question
* expected SQL
* generated SQL
* result metadata
* schema context

---

## Judge Outputs (0–1 scoring per dimension):

1. table_selection_correctness
2. sql_semantic_equivalence
3. result_correctness
4. hallucination_detection

---

## Final Score:

weighted_score = weighted sum of all dimensions

---

# 📊 SCORING SYSTEM (CORE)

Each evaluation produces:

* per-question score
* per-dimension breakdown
* overall dataset score

---

## Additional Metrics:

* execution success rate
* empty result rate
* refinement iterations
* latency

---

# 📈 EVALUATION REPORTING (MANDATORY)

Generate structured reports:

---

## Per Run:

* overall score
* pass rate
* failure breakdown:

  * wrong table
  * wrong join
  * wrong filter
  * execution error
  * empty result

---

## Per Table:

* historical performance trend
* regression detection
* weak query types

---

## Per Dataset:

* coverage:

  * simple
  * complex
  * join
  * geo
* gaps

---

# 🔁 REGRESSION SYSTEM

Before publishing a table:

* Run evaluation on:

  1. its dataset
  2. ALL production datasets

---

## Regression Rule:

* If score drops > threshold → block publish

---

# 🔍 LANGFUSE INTEGRATION (MANDATORY)

Langfuse is the central observability system.

---

## You MUST:

1. Log every agent execution as a trace
2. Attach:

   * prompt version
   * dataset id
   * evaluation id
3. Store:

   * inputs
   * outputs
   * intermediate steps

---

## Dataset Integration:

* Each evaluation run must create a Langfuse dataset run
* Each question → trace

---

## Required Tags:

* table_id
* dataset_version
* prompt_version
* user_id (if real query)

---

# 🧠 CONFIDENCE SCORING SYSTEM

Compute confidence_score ∈ [0,1] using:

* retrieval confidence
* schema match
* execution success
* validation checks
* historical performance

---

# ⚠️ WARNING SYSTEM

Generate warnings:

* low confidence
* ambiguous tables
* high cost query
* empty result anomaly
* schema mismatch

---

# 🔁 FEEDBACK LOOP (CRITICAL)

Ingest feedback:

* positive / negative
* user corrections

---

## Use feedback to:

* identify weak queries
* enrich datasets
* prioritize evaluation

---

# 📉 FAILURE INTELLIGENCE ENGINE

Cluster failures into:

* wrong_table
* wrong_join
* wrong_filter
* hallucination
* execution_error

---

## Use clusters to:

* generate new dataset entries
* improve prompts
* detect systemic issues

---

# 🔁 DATASET EVOLUTION

Automatically:

* add failed queries to dataset
* mutate existing queries
* expand edge cases

---

# 🔗 CROSS-TABLE REASONING

Use:

* join graph
* profiling overlap
* enrichment join_keys

To:

* improve multi-table queries
* reduce join errors

---

# 🧠 RESULT VALIDATION (ENHANCED)

Must include:

* shape validation
* distribution checks
* anomaly detection
* zero-result recovery

---

# 📊 METRICS (GLOBAL)

Track:

* execution accuracy
* evaluation score
* confidence calibration
* failure rate
* feedback alignment
* latency

---

# 📁 OUTPUT

Generate:

1. Full LangGraph flow (nodes + transitions)
2. Prompt templates:

   * Composer
   * Refiner
   * Judge
3. Dataset schema & lifecycle
4. Evaluation pipeline design
5. Scoring formulas
6. Langfuse integration structure
7. Failure taxonomy

---

# ⚠️ CRITICAL REQUIREMENTS

* No mock evaluations — real execution only
* Fully traceable system (Langfuse)
* Deterministic evaluation pipeline
* Modular design (each component replaceable)

---

# 🧠 FINAL GOAL

Build a system that:

* Learns from its failures
* Improves over time
* Is explainable and auditable
* Provides strong guarantees before production exposure
