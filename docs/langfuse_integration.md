# Langfuse Integration — Observability Structure

## Overview

Langfuse is the **central observability and dataset management system** for the TextToSQL platform. Every agent execution, evaluation run, and dataset version is tracked here. No component runs without emitting a trace.

---

## Trace Structure

Every agent execution creates one Langfuse **trace** with nested **spans** per node.

### Top-Level Trace

```python
langfuse.trace(
    name="texttosql_agent",
    user_id=state.user_id,
    session_id=state.session_id,
    metadata={
        "table_id": state.selected_table_id,
        "dataset_version": dataset.version if in_eval else None,
        "eval_run_id": eval_run.id if in_eval else None,
        "prompt_version": state.prompt_version,
        "environment": settings.APP_ENV,  # "sandbox" | "production"
    },
    tags=[
        f"table:{state.selected_table_id}",
        f"lang:{state.language}",
        f"prompt_v:{state.prompt_version}",
        "sandbox" if in_eval else "production",
    ]
)
```

---

## Node Spans

Each LangGraph node emits a child span:

### QueryNormalizer Span

```python
with trace.span(name="query_normalizer") as span:
    span.update(
        input={"raw_query": state.raw_query},
        output={
            "normalized_query": state.normalized_query,
            "expansion_map": state.expansion_map,
            "unknown_acronyms": state.unknown_acronyms,
        },
        metadata={"duration_ms": elapsed}
    )
```

### TableDiscovery Span

```python
with trace.span(name="table_discovery") as span:
    span.update(
        input={"query": state.normalized_query, "scope": state.active_scope_id},
        output={
            "candidates": state.candidates,
            "selected_table": state.selected_table_id,
            "retrieval_confidence": state.retrieval_confidence,
            "disambiguation_flag": state.disambiguation_flag,
        },
        metadata={"k_returned": len(state.candidates)}
    )
```

### SQLComposer Span (LLM Generation)

```python
with trace.generation(name="sql_composer") as gen:
    gen.update(
        model=model_name,
        model_parameters={"temperature": 0.0, "max_tokens": 1500},
        prompt=composer_prompt,          # full prompt string (logged for debugging)
        completion=raw_llm_response,
        usage={"input": prompt_tokens, "output": completion_tokens},
        metadata={"prompt_version": state.prompt_version}
    )
```

### SQLRefiner Span (per iteration)

```python
with trace.span(name=f"sql_refiner_iter_{i}") as span:
    span.update(
        input={"failed_sql": state.generated_sql, "error": state.last_error},
        output={"corrected_sql": corrected_sql},
        metadata={"iteration": i, "root_cause": root_cause}
    )
```

### ResultValidator Span

```python
with trace.span(name="result_validator") as span:
    span.update(
        input={"sql": state.generated_sql},
        output={
            "validated": state.validated,
            "row_count": result_row_count,
            "warnings": state.warnings,
            "zero_result_recovery": state.zero_result_recovery,
        }
    )
```

### ConfidenceScorer Span

```python
with trace.span(name="confidence_scorer") as span:
    span.update(
        output={
            "confidence_score": state.confidence_score,
            "breakdown": state.score_breakdown,
        }
    )
```

---

## Evaluation Dataset Integration

### Dataset Creation in Langfuse

Each table dataset is mirrored as a **Langfuse Dataset**:

```python
langfuse_dataset = langfuse.create_dataset(
    name=f"table_{table_id}_v{dataset.version}",
    metadata={
        "table_id": table_id,
        "dataset_version": dataset.version,
        "source": dataset.source,
        "question_count": dataset.question_count,
    }
)

for question in dataset.questions:
    langfuse_dataset.create_item(
        input={"question": question.question, "schema_context": schema_block},
        expected_output={"sql": question.expected_sql, "result_shape": question.expected_result_shape},
        metadata={
            "question_id": question.id,
            "difficulty": question.difficulty,
            "question_type": question.question_type,
            "weight": question.weight,
        }
    )
```

### Evaluation Run in Langfuse

Each eval run creates a **Langfuse Dataset Run**:

```python
langfuse_run = langfuse_dataset.create_run(
    name=f"eval_run_{eval_run.id}",
    metadata={
        "eval_run_id": eval_run.id,
        "prompt_version": current_prompt_version,
        "triggered_by": "sandbox_eval | publish_gate | regression | nightly",
    }
)
```

Each question links its trace to the dataset run:

```python
langfuse_run.link(
    trace_or_observation=question_trace,
    dataset_item_id=langfuse_dataset_item.id,
    metadata={
        "question_id": question.id,
        "final_score": question.final_score,
        "failure_type": question.failure_type,
    }
)
```

---

## Prompt Version Management

All agent prompts are stored in Langfuse Prompt Management:

| Prompt Name | Tags |
|---|---|
| `texttosql_composer_v{N}` | `production` or `canary` |
| `texttosql_refiner_v{N}` | `production` or `canary` |
| `texttosql_judge_v{N}` | `production` |

### Version Resolution at Agent Startup

```python
def get_active_prompt(name: str, user_id: str) -> tuple[str, str]:
    """Returns (prompt_text, prompt_version)."""
    canary_config = config.get("canary_traffic_pct", 0)
    use_canary = (hash(user_id) % 100) < canary_config

    if use_canary:
        prompt = langfuse.get_prompt(name, label="canary")
    else:
        prompt = langfuse.get_prompt(name, label="production")

    return prompt.compile(**context_vars), f"{name}:{prompt.version}"
```

---

## Metrics Tracked in Langfuse

For each trace, the following **scores** are logged (via `langfuse.score()`):

```python
# Runtime scores
langfuse.score(trace_id=tid, name="confidence_score",       value=state.confidence_score)
langfuse.score(trace_id=tid, name="refiner_iterations",     value=state.refiner_iterations)
langfuse.score(trace_id=tid, name="execution_time_ms",      value=state.execution_time_ms)

# Evaluation scores (only for eval runs)
langfuse.score(trace_id=tid, name="final_eval_score",              value=question.final_score)
langfuse.score(trace_id=tid, name="table_selection_correctness",   value=judge_output["table_selection_correctness"])
langfuse.score(trace_id=tid, name="sql_semantic_equivalence",      value=judge_output["sql_semantic_equivalence"])
langfuse.score(trace_id=tid, name="result_correctness",            value=judge_output["result_correctness"])
langfuse.score(trace_id=tid, name="hallucination_detected",        value=1.0 if judge_output["hallucination_detected"] else 0.0)
```

---

## Required Langfuse Dashboard Views

| View | Metrics |
|---|---|
| **Per Table** | avg confidence, avg score, failure rate by type |
| **Per Dataset Version** | score trend, coverage by question type |
| **Prompt A/B** | compare production vs canary on all 4 judge dimensions |
| **Regression Monitor** | score delta vs previous eval run per production table |
| **Failure Cluster** | group traces by failure_type, drill into examples |

---

## Tags Reference

| Tag | Value |
|---|---|
| `table:{table_id}` | Always |
| `lang:{language}` | `he` / `en` / `mixed` |
| `prompt_v:{version}` | Composer prompt version |
| `env:{environment}` | `sandbox` / `production` |
| `eval_run:{run_id}` | Only on evaluation runs |
| `triggered_by:{cause}` | `user` / `eval` / `regression` |

---

## Failure Intelligence → Langfuse Feedback Loop

When a failure cluster is detected:

```python
# Flag for human review in Langfuse
langfuse.score(
    trace_id=tid,
    name="needs_review",
    value=1.0,
    comment=f"Cluster: {failure_type} (#{cluster_count} occurrences this week)"
)
```

Platform team uses Langfuse's annotation interface to:
1. Confirm failure classification
2. Tag for prompt improvement
3. Approve addition to dataset

This closes the feedback loop: **user failure → Langfuse annotation → dataset evolution → better evaluation → prompt improvement → re-evaluation**.
