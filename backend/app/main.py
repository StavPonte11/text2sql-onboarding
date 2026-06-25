import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager

from core.db.engine import create_db_and_tables, engine
from core.logging import bind_request_context, clear_request_context
from core.metrics import PROMETHEUS_REGISTRY
from core.metrics_middleware import PrometheusMiddleware
from core.models.models import AuditQuery
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlmodel import Session

from app.config import settings
from app.routers import (
    admin_approval,
    admin_auth,
    agent,
    audit,
    enrichment,
    evaluation,
    extractors,
    feedback,
    health,
    orchestration,
    profiling,
    publish,
    questions,
    scopes,
    tables,
)
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG if settings.APP_ENV == "development" else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=False,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_db_and_tables()
        start_scheduler()
    except Exception as e:
        logger.error(f"Startup error: {e}", exc_info=True)
        raise e
    yield
    stop_scheduler()


app = FastAPI(
    title="Text2SQL Studio API",
    description="Data Intelligence module — Evaluation Orchestration & Monitoring",
    version="2.0.0",
    lifespan=lifespan,
)

# Register Prometheus metrics tracking middleware
app.add_middleware(PrometheusMiddleware)


@app.middleware("http")
async def context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    session_id = (
        request.headers.get("x-session-id")
        or request.query_params.get("session_id")
        or str(uuid.uuid4())
    )
    user_id = request.headers.get("x-user-id") or "user-1"
    langfuse_trace_id = request.headers.get("x-langfuse-trace-id")

    bind_request_context(
        session_id=session_id,
        user_id=user_id,
        request_id=request_id,
        langfuse_trace_id=langfuse_trace_id,
    )
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        clear_request_context()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/metrics")
def metrics() -> Response:
    """
    Exposes Prometheus metrics gathered from the custom isolated collector registry.
    """
    return Response(
        content=generate_latest(PROMETHEUS_REGISTRY), media_type=CONTENT_TYPE_LATEST
    )


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time_ms = int((time.time() - start_time) * 1000)

    # Only log table-related routes to avoid spamming
    if request.url.path.startswith("/api/tables") and request.method in [
        "POST",
        "PUT",
        "DELETE",
        "GET",
    ]:
        # Extract table_id if present in path (e.g. /api/tables/{table_id}/...)
        path_parts = request.url.path.split("/")
        table_id = (
            path_parts[3] if len(path_parts) > 3 and path_parts[3] != "eval" else None
        )

        # In a real app, user_id comes from auth token
        user_id = "user-1"
        query_desc = f"{request.method} {request.url.path}"

        try:
            with Session(engine) as session:
                audit = AuditQuery(
                    table_id=table_id
                    if table_id and len(table_id) == 36
                    else None,  # quick check for uuid
                    user_id=user_id,
                    raw_question=query_desc,
                    execution_time_ms=process_time_ms,
                    status="success" if response.status_code < 400 else "error",
                )
                session.add(audit)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to log audit: {e}")

    return response


api_router = APIRouter(prefix="/api")
api_router.include_router(tables.router)
api_router.include_router(enrichment.router)
api_router.include_router(questions.router)
api_router.include_router(evaluation.router)
api_router.include_router(extractors.router)
api_router.include_router(orchestration.router)
api_router.include_router(publish.router)
api_router.include_router(scopes.router)
api_router.include_router(audit.router)
api_router.include_router(profiling.router)
api_router.include_router(feedback.router)
api_router.include_router(health.router)
api_router.include_router(admin_auth.router)
api_router.include_router(admin_approval.router)
api_router.include_router(agent.router)

app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
