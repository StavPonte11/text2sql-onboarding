import logging
import time
from contextlib import asynccontextmanager

from core.db.engine import create_db_and_tables, engine
from core.models.models import AuditQuery
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from python_core_utils import setup_logging
from python_core_utils.logging import CorrelationIdMiddleware
from sqlmodel import Session
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import auth as auth_api
from app.config import auth_settings, settings
from app.core.auth import get_current_user
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

setup_logging(
    log_level="DEBUG" if settings.APP_ENV == "development" else "INFO",
    logger_names=["app", "uvicorn", "fastapi"],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.LLM_API_KEY:
        logger.warning("Startup failed: LLM_API_KEY is missing. LLM judge cannot run.")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth_settings.SESSION_SECRET_KEY,
    session_cookie=auth_settings.SESSION_COOKIE_NAME,
    max_age=auth_settings.SESSION_COOKIE_MAX_AGE,
)

# Trust X-Forwarded-Proto from reverse proxies (fixes HTTPS redirect issue in Keycloak SSO)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Attach CorrelationId and log every request via python_core_utils
app.add_middleware(CorrelationIdMiddleware)


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
                    table_id=(
                        table_id if table_id and len(table_id) == 36 else None
                    ),  # quick check for uuid
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

# Public endpoints
api_router.include_router(health.router)
api_router.include_router(admin_auth.router)
api_router.include_router(admin_approval.router)
api_router.include_router(agent.router)
api_router.include_router(auth_api.router, prefix="/v1/auth", tags=["auth"])

# Private endpoints (SSO protected)
auth_deps = [Depends(get_current_user)]
private_router = APIRouter(dependencies=auth_deps)

private_router.include_router(tables.router)
private_router.include_router(enrichment.router)
private_router.include_router(questions.router)
private_router.include_router(evaluation.router)
private_router.include_router(extractors.router)
private_router.include_router(orchestration.router)
private_router.include_router(publish.router)
private_router.include_router(scopes.router)
private_router.include_router(audit.router)
private_router.include_router(profiling.router)
private_router.include_router(feedback.router)
private_router.include_router(admin_approval.router)

api_router.include_router(private_router)
app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
