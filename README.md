# TextToSQL Studio ⚡

A comprehensive, self-service governance platform for managing, enriching, and testing database tables utilized by natural language processing and TextToSQL agents.

## 🚀 Quick Start

### Backend (Python/FastAPI)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --port 8000 --reload
```

### Frontend (React/Vite)
```bash
cd frontend
npm install
npm run dev
```

## 📖 Documentation Index

Dive deeper into the design and lifecycle of the platform:

- [Project Description & Mission](./docs/project_description.md)
- [System Architecture](./docs/architecture.md)
- [Feature Checklist](./docs/features.md)
- [Roadmap & TODOs](./docs/roadmap_and_todos.md)
- [AI Onboarding Guidelines](./docs/developer_context.md)