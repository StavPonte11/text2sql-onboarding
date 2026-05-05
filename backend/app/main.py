from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session
import time
from app.config import settings
from app.db.engine import create_db_and_tables, engine
from app.models.models import AuditQuery
from app.routers import tables, enrichment, questions, evaluation, publish, scopes, audit, profiling, feedback, health
from app.routers import orchestration
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Text2SQL Studio API",
    description="Data Intelligence module — Evaluation Orchestration & Monitoring",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time_ms = int((time.time() - start_time) * 1000)

    # Only log table-related routes to avoid spamming
    if request.url.path.startswith("/tables") and request.method in ["POST", "PUT", "DELETE", "GET"]:
        # Extract table_id if present in path (e.g. /tables/{table_id}/...)
        path_parts = request.url.path.split("/")
        table_id = path_parts[2] if len(path_parts) > 2 and path_parts[2] != "eval" else None
        
        # In a real app, user_id comes from auth token
        user_id = "user-1" 
        query_desc = f"{request.method} {request.url.path}"

        try:
            with Session(engine) as session:
                audit = AuditQuery(
                    table_id=table_id if table_id and len(table_id) == 36 else None, # quick check for uuid
                    user_id=user_id,
                    raw_question=query_desc,
                    execution_time_ms=process_time_ms,
                    status="success" if response.status_code < 400 else "error"
                )
                session.add(audit)
                session.commit()
        except Exception as e:
            print(f"Failed to log audit: {e}")

    return response



app.include_router(tables.router)
app.include_router(enrichment.router)
app.include_router(questions.router)
app.include_router(evaluation.router)
app.include_router(orchestration.router)
app.include_router(publish.router)
app.include_router(scopes.router)
app.include_router(audit.router)
app.include_router(profiling.router)
app.include_router(feedback.router)
app.include_router(health.router)


@app.get("/health")
def health():
    return {"status": "ok"}
