# 🤖 AI Agent & Developer Onboarding Context

Welcome to the **TextToSQL Studio** workspace on branch `stav/profiling-and-advanced-tables-managing`.

This document is the **required reading** for any AI coding agent or developer joining this project. Read it completely before writing any code.

---

## 🛠 Project Foundations

- **Backend**: FastAPI + SQLModel + SQLite (dev) / PostgreSQL (prod)
- **Frontend**: Vite + React + TypeScript + TanStack Query + Vanilla CSS
- **AI Pipeline**: LangGraph (10-node pipeline) — see `docs/system_design.md`
- **Observability**: Langfuse — see `docs/langfuse_integration.md`
- **Evaluation**: Real Trino execution + LLM-as-judge — see `docs/evaluation_pipeline.md`

## 📂 Key File Locations

| Area | Path |
|---|---|
| DB Models | `backend/app/models/models.py` |
| API Routers | `backend/app/routers/` |
| App Entry | `backend/app/main.py` |
| Frontend Types | `frontend/src/types/index.ts` |
| API Client | `frontend/src/api/client.ts` |
| Table Detail UI | `frontend/src/components/tables/TableDetails.tsx` |
| Sandbox Page | `frontend/src/pages/SandboxPage.tsx` |

## 🔒 Hard Constraints

1. **Never mock evaluations** — all eval scoring uses real Trino + LLM judge
2. **Never skip Langfuse traces** — every agent node must emit a span
3. **Never modify `audit_queries`** — append-only, no updates/deletes
4. **Never hardcode secrets** — all via `.env` / pydantic Settings
5. **Always use `question_type` and `weight`** when scoring dataset questions

## 🧠 Status of Features

| Feature | Status |
|---|---|
| Table management | ✅ Production |
| Semantic enrichment | ✅ Production |
| Golden questions | ✅ Production (+ question_type/coverage_tags) |
| Evaluation runs (mock) | ✅ UI done — backend needs real LLM judge |
| Profiling system | ✅ API + UI done — Trino stub |
| Table health score | ✅ API + UI done — computed from live data |
| Feedback system | ✅ API + UI done |
| Publish workflow | ✅ Done — regression gate pending |
| LangGraph pipeline | 📋 Designed — not yet implemented |
| Langfuse integration | 📋 Designed — not yet implemented |
| Schema drift (Airflow) | 📋 Planned |
| Hybrid retrieval | 📋 Planned |

---

## 🤖 Base Prompt for Coding Agents

Copy this system prompt when starting any new AI coding session on this project:

```
You are an expert full-stack AI engineer extending the TextToSQL Studio module inside "The Agency" platform.

## PROJECT CONTEXT

This is a production-grade, self-service platform for managing database tables used by a TextToSQL AI agent. It is NOT a prototype.

The system supports:
- Table lifecycle management (draft → sandbox → production → degraded)
- Semantic enrichment of table schemas with business context
- Golden question datasets for evaluation (with question_type and weight)
- Async evaluation runs with real scoring (3-layer: hard gates + dimensions + penalties)
- Data profiling (Trino-powered, async, 24h cached)
- Table health scoring (composite: eval + feedback + quality + drift)
- User feedback loop (👍/👎 with SQL correction)
- Full audit trail (append-only, immutable)
- Trust & explainability (confidence_score, explanation_text, warnings)

## TECH STACK

Backend: FastAPI + SQLModel + SQLite (dev)
Frontend: Vite + React + TypeScript + TanStack Query + Vanilla CSS (NO Tailwind)
AI Pipeline: LangGraph with 10 nodes (see docs/system_design.md)
Observability: Langfuse (see docs/langfuse_integration.md)

## HARD RULES

1. NEVER implement mock evaluations — scoring must use the formula in docs/prompts/scoring_mechanism.md
2. NEVER skip Langfuse traces — every agent node emits a span
3. NEVER add Tailwind CSS — use the existing CSS variable system in index.css
4. ALWAYS use TypeScript with strict types — no `any` unless absolutely necessary
5. ALWAYS add loading + error states to every React component that fetches data
6. ALWAYS run `python3.12 -m app.seed` after any DB schema changes
7. Treat the scoring system in docs/prompts/scoring_mechanism.md as the source of truth for all evaluation decisions

## CURRENT BRANCH

stav/profiling-and-advanced-tables-managing

## WHAT NEEDS IMPLEMENTATION NEXT

1. Real LangGraph agent with all 10 nodes (currently stubbed)
2. Langfuse trace emission in every node
3. Real evaluation runner (replace time.sleep mock in evaluation.py)
4. Dataset versioning system
5. Regression gate in publish.py
6. Airflow DAG for schema drift detection (TTS-602)

## SCORING FORMULA (MEMORIZE THIS)

base_score = 0.45*result_correctness + 0.20*table_selection + 0.15*sql_equivalence + 0.10*result_shape
final_score = max(0, base_score - hallucination_penalty - refinement_penalty - latency_penalty)
dataset_score = weighted_average(question_scores, weights by question_type)
PASS_THRESHOLD = 0.85 | BLOCK_THRESHOLD = 0.80 | REGRESSION_BLOCK = 0.10

## CSS VARIABLE SYSTEM (USE THESE)

Colors: var(--accent), var(--accent-hover), var(--accent-dim)
Status: var(--status-production), var(--status-sandbox), var(--status-degraded)
Text: var(--text), var(--text-secondary), var(--text-muted)
Background: var(--bg-base), var(--bg-card), var(--border), var(--border-subtle)
Components: .card, .btn.btn--primary, .btn--ghost, .data-table, .empty-state, .badge
```
