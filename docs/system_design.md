# TextToSQL System Design — Full Architecture

## Overview

This document describes the complete AI system architecture for the production-grade TextToSQL platform inside **The Agency**. Every component is designed to be modular, traceable, and continuously improving.

---

## 🔄 End-to-End Pipeline (LangGraph Nodes)

```
User Query
    │
    ▼
[1] QueryNormalizer
    │ normalized_query, expansion_map, unknown_acronyms[]
    ▼
[2] TableDiscovery
    │ candidate_tables[] (hybrid BM25 + pgvector, ACL filtered)
    ▼
[3] DisambiguationCheck  ──── needs_disambiguation? ──── [PAUSE → UI clarification]
    │ selected_table
    ▼
[4] ContextBuilder
    │ schema, enrichment, profiling stats, join hints, jargon
    ▼
[5] SQLComposer
    │ generated_sql, plan, tables_used[]
    ▼
[6] SQLRefiner  ◄──── retry (max 4) ────┐
    │                                    │
    │ Trino execution                    │ execution_error
    │                                    │
    ▼                                    │
[7] ResultValidator ─────────────────────┘ (on shape/empty anomaly)
    │ validated_result, warnings[]
    ▼
[8] ConfidenceScorer
    │ confidence_score ∈ [0,1], score_breakdown
    ▼
[9] InsightGenerator
    │ explanation_text, warnings[], suggested_next_steps[]
    ▼
Response to User
    │
    ▼
[10] AuditLogger (always runs, even on failure)
     │ AuditQuery record written to PostgreSQL + Langfuse trace closed
```

---

## Node Specifications

### [1] QueryNormalizer

**Inputs:** raw user query (Hebrew/English)

**Operations:**
- Regex scan for Military Hebrew acronym patterns
- Dictionary lookup against jargon JSON
- Output normalized_query with expansion_map

**Outputs:**
```python
{
  "normalized_query": str,
  "expansion_map": dict[str, str],   # {"אמ\"ן": "Military Intelligence Directorate"}
  "unknown_acronyms": list[str],
  "language": "he" | "en" | "mixed"
}
```

**SLA:** < 50ms p99

---

### [2] TableDiscovery

**Inputs:** normalized_query, user_id, active_scope?

**Operations:**
1. BM25 search over Elasticsearch index (table_name, descriptions, example_questions)
2. Cosine similarity via pgvector (embedded query vs embedded table descriptions)
3. RRF fusion (Reciprocal Rank Fusion)
4. Hard ACL filter (remove tables user cannot access)
5. Return top-K (≤ 8)

**Outputs:**
```python
{
  "candidates": [
    {"table_id": str, "score": float, "retrieval_source": "bm25|vector|both"}
  ],
  "retrieval_confidence": float,  # highest candidate score, normalized
  "disambiguation_flag": bool     # true if top-2 from different domains within 0.05
}
```

---

### [3] DisambiguationCheck

**Condition:** `disambiguation_flag=True`

**Action:** Generate one-sentence clarification question (lightweight LLM call), surface as clickable choice card in UI.

On user selection → pin table, skip to ContextBuilder.

---

### [4] ContextBuilder

**Inputs:** selected table_id, normalized_query

**Assembles prompt context in order:**

1. **Schema block** — column names, types, nullable flags (from enrichment_versions)
2. **Semantic enrichment** — table_description, column_descriptions, known_enums
3. **Profiling block** — top values per column, null rates, cardinality hints, min/max ranges
4. **Join hints** — cross_table_profiles with match_strength="strong"
5. **Jargon block** — top-10 relevant entries (or full dict < 800 tokens)
6. **Example questions** — up to 3 golden questions from the dataset

**Token budget:** target < 6000 tokens for context; truncate profiling block if exceeded.

**Outputs:** structured `context_payload` dict passed to Composer.

---

### [5] SQLComposer

**Inputs:** normalized_query, context_payload

**Prompt:** See `docs/prompts/composer_prompt.md`

**Outputs:**
```python
{
  "generated_sql": str,
  "tables_used": list[str],
  "plan": str,               # chain-of-thought reasoning
  "expected_columns": list[str],
  "prompt_version": str      # from Langfuse prompt registry
}
```

---

### [6] SQLRefiner

**Inputs:** generated_sql, execution_error (if any), iteration_count

**Circuit breaker:** hard stop at `MAX_ITERATIONS = 4`

On iteration 4 failure → return structured error:
```python
{
  "error": "max_refinements_exceeded",
  "last_sql": str,
  "last_error": str,
  "user_explanation": str   # in user's language
}
```

**Prompt:** See `docs/prompts/refiner_prompt.md`

**Trino integration:**
- Tag all queries: `user_id`, `agent=texttosql`, `table_id`
- Resource group limits: 2 concurrent/user, 8GB max memory
- Run EXPLAIN before execution if estimated scan > 10GB → warn user

---

### [7] ResultValidator

**Checks (in order):**

1. **Execution success** — did Trino return a result?
2. **Shape validation** — do returned columns match expected_columns from Composer plan?
3. **Zero-result check** — if empty + non-trivial WHERE → trigger relaxed retry (drop one condition at a time)
4. **Large result warning** — > 100k rows on non-aggregate query → emit warning
5. **Distribution anomaly** — compare result stats against profiling expected ranges

**Outputs:**
```python
{
  "validated": bool,
  "result": Any,
  "warnings": list[str],
  "zero_result_recovery": dict | None
}
```

---

### [8] ConfidenceScorer

Uses the formula from `docs/prompts/scoring_mechanism.md`:

```python
confidence_score = (
    0.30 * retrieval_confidence +
    0.20 * schema_match_score +      # % of generated columns found in schema
    0.20 * execution_success +        # 1.0 if executed, 0.0 if failed
    0.20 * historical_success_rate +  # from table_health.eval_success_rate
    0.10 * validation_checks_passed   # fraction of validator checks passed
)
```

**Hard rule:** If execution failed → confidence_score = 0.0 regardless of other signals.

---

### [9] InsightGenerator

**Generates:**
- `explanation_text`: 1–3 sentence natural language explanation of how the SQL was built and which tables were used (in user's language)
- `warnings[]`: list of human-readable warning strings
- `suggested_next_steps[]`: optional follow-up question suggestions

---

### [10] AuditLogger

Writes to `audit_queries` table:
- All input/output fields
- `confidence_score`, `explanation_text`, `warnings_json`
- `langfuse_trace_id` for direct navigation
- `refiner_iterations`, `execution_time_ms`, `result_row_count`

**Immutability:** PostgreSQL row-level security — no UPDATE or DELETE on `audit_queries`.

---

## State Object (LangGraph)

```python
class AgentState(TypedDict):
    # Input
    raw_query: str
    user_id: str
    session_id: str
    active_scope_id: str | None

    # Step 1 — Normalization
    normalized_query: str
    expansion_map: dict
    unknown_acronyms: list[str]
    language: str

    # Step 2 — Discovery
    candidates: list[dict]
    selected_table_id: str
    retrieval_confidence: float
    disambiguation_flag: bool

    # Step 4 — Context
    context_payload: dict
    prompt_version: str

    # Step 5 — Composition
    generated_sql: str
    tables_used: list[str]
    plan: str
    expected_columns: list[str]

    # Step 6 — Refinement
    refiner_iterations: int
    last_error: str | None

    # Step 7 — Validation
    result: Any
    warnings: list[str]
    validated: bool

    # Step 8 — Confidence
    confidence_score: float
    score_breakdown: dict

    # Step 9 — Insight
    explanation_text: str
    suggested_next_steps: list[str]

    # Step 10 — Audit
    audit_id: str
    langfuse_trace_id: str
```

---

## Error Routing

```
execution_error (iter < 4) ──────────────────── → SQLRefiner
execution_error (iter = 4) ──────────────────── → AuditLogger(status=error)
disambiguation needed ────────────────────────── → PAUSE (wait for UI input)
authorization failure ────────────────────────── → AuditLogger(status=auth_denied)
timeout (> 120s) ─────────────────────────────── → cancel Trino + AuditLogger(status=timeout)
```
