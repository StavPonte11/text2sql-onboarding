# TextToSQL Studio ⚡

A comprehensive, self-service governance platform for managing, enriching, and testing database tables utilized by natural language processing and TextToSQL agents.

## ⚙️ Prerequisites

Before starting, ensure you have `uv` and `pnpm` installed:

- **uv** (Python Package Manager):
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # If 'uv' is not found immediately, restart your terminal or run:
  source $HOME/.local/bin/env
  ```
- **pnpm** (Node Package Manager):
  ```bash
  npm install -g pnpm
  ```

## 🚀 Quick Start

### Backend (Python/FastAPI)
```bash
cd backend
uv sync
source .venv/bin/activate
python -m app.seed
uvicorn app.main:app --port 8000 --reload
```

### Frontend (React/Vite)
```bash
cd frontend
pnpm install
pnpm dev
```

## 📖 Documentation Index

### Platform & Architecture
- [Project Description & Mission](./docs/project_description.md)
- [System Architecture](./docs/architecture.md)
- [Feature Checklist](./docs/features.md)
- [Roadmap & TODOs](./docs/roadmap_and_todos.md)
- [Developer & Agent Onboarding](./docs/developer_context.md)

### AI System Design
- [Full System Design — LangGraph Pipeline](./docs/system_design.md)
- [Evaluation Pipeline & Dataset Lifecycle](./docs/evaluation_pipeline.md)
- [Langfuse Integration](./docs/langfuse_integration.md)

### Prompt Templates
- [Composer Prompt](./docs/prompts/composer_prompt.md)
- [Refiner Prompt](./docs/prompts/refiner_prompt.md)
- [LLM Judge Prompt](./docs/prompts/judge_prompt.md)
- [Scoring Mechanism](./docs/prompts/scoring_mechanism.md)