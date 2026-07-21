"""
profiling.py — Production-grade profiling router.

Replaces all stubs with real Trino-backed execution via profiling_engine.
New endpoint: GET /tables/{id}/profile/context — LLM-ready context blob.
"""

from core.models.models import EnrichmentVersion
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from app.config import settings

from core.db.engine import engine, get_session
from core.models.models import (
    ColumnProfile,
    ColumnProfileRead,
    CrossTableProfile,
    CrossTableProfileRead,
    ProfilingRun,
    ProfilingStatus,
    Table,
    TableProfile,
    TableProfileRead,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.services.join_detection import discover_joins_for_table
from core.services.profiling_engine import build_context_for_llm

logger = logging.getLogger(__name__)
router = APIRouter(tags=["profiling"])


def _upsert_ai_summary(session: Session, table_id: str, summary: str) -> None:
    """
    Write the LLM-generated summary into EnrichmentVersion.
    - If no enrichment exists yet: creates version 1 with ai_summary.
    - If a human table_description exists: stores under 'ai_summary' key (non-destructive).
    - If no human description: also copies to 'table_description' so the agent can find it.
    """

    existing = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(EnrichmentVersion.version.desc())
    ).first()

    next_version = (existing.version + 1) if existing else 1
    existing_data: dict = dict(existing.data) if (existing and existing.data) else {}

    has_human_description = bool(existing_data.get("table_description", "").strip())

    new_data = dict(existing_data)
    new_data["ai_summary"] = summary
    if not has_human_description:
        # Promote ai_summary to table_description only when no human annotation exists
        new_data["table_description"] = summary

    ev = EnrichmentVersion(
        table_id=table_id,
        version=next_version,
        data=new_data,
    )
    session.add(ev)
    session.commit()
    logger.info(
        "[Profiling] Stored AI summary for table %s (v%d)", table_id, next_version
    )


# ── Background worker ──────────────────────────────────────────────────────────
# ── Background worker ──────────────────────────────────────────────────────────
async def trigger_temporal_profiling_workflow(table_id: str, resume_from_partial: bool = False) -> bool:
    """
    Attempts to trigger the profiling workflow via Temporal.
    Returns True if started successfully, False otherwise.
    """
    try:
        logger.info("[Profiling] Connecting to Temporal client at %s", settings.TEMPORAL_HOST)
        client = await Client.connect(settings.TEMPORAL_HOST)
        await client.start_workflow(
            "TableProfilingWorkflow",
            id=f"profile-{table_id}",
            task_queue="profiling-tasks",
            args=[table_id, resume_from_partial],
        )
        logger.info("[Profiling] Successfully started Temporal workflow for table %s", table_id)
        return True
    except WorkflowAlreadyStartedError:
        logger.info("[Profiling] Profiling workflow is already running for table %s", table_id)
        return True
    except Exception as e:
        logger.error("[Profiling] Failed to start Temporal workflow for table %s: %s", table_id, e)
        return False


# ── GET /tables/{id}/profile ───────────────────────────────────────────────────
@router.get("/tables/{table_id}/profile", response_model=TableProfileRead)
async def get_table_profile(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    latest_run = session.exec(
        select(ProfilingRun).where(ProfilingRun.table_id == table_id).order_by(ProfilingRun.started_at.desc())
    ).first()

    if not profile and not latest_run:
        raise HTTPException(
            status_code=404, detail="No profile found. Run POST /profile/run first."
        )

    # Sync state from Temporal if stuck
    if latest_run and latest_run.status in (ProfilingStatus.running, ProfilingStatus.pending):
        try:
            client = await Client.connect(settings.TEMPORAL_HOST)
            handle = client.get_workflow_handle(f"profile-{table_id}")
            desc = await handle.describe()
            if desc.status in (
                WorkflowExecutionStatus.FAILED,
                WorkflowExecutionStatus.TERMINATED,
                WorkflowExecutionStatus.TIMED_OUT,
            ):
                latest_run.status = ProfilingStatus.failed
                session.add(latest_run)
                session.commit()
                session.refresh(latest_run)
            elif desc.status == WorkflowExecutionStatus.CANCELED:
                latest_run.status = ProfilingStatus.canceled
                session.add(latest_run)
                session.commit()
                session.refresh(latest_run)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                latest_run.status = ProfilingStatus.failed
                session.add(latest_run)
                session.commit()
                session.refresh(latest_run)
            else:
                logger.warning("[Profiling] Failed to sync temporal status for %s: %s", table_id, e)
        except Exception as e:
            logger.warning("[Profiling] Failed to sync temporal status for %s: %s", table_id, e)

    # If latest_run is missing but we bypassed the 404, it means we have legacy TableProfile data 
    # without a run history. In this case, we default the status to completed.
    status = latest_run.status if latest_run else ProfilingStatus.completed

    # If we have a failed or running latest_run but no TableProfile data yet, 
    # return a stub dictionary so the frontend can still display the run's status/error.
    profile_dict = profile.model_dump() if profile else {
        "id": "pending",
        "table_id": table_id,
        "row_count": None,
        "sample_size": None,
        "column_count": None,
        "size_bytes": None,
        "null_rate_avg": None,
        "duplicate_rate": None,
        "sample_data": None,
        "profile_json": None,
        "cached_until": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    return TableProfileRead(
        **profile_dict,
        status=status,
        is_partial=False
    )


# ── POST /tables/all/profile/run ──────────────────────────────────────────────
@router.post("/tables/all/profile/run", status_code=202)
async def run_all_profiles(
    background_tasks: BackgroundTasks,
    force: bool = False,
    session: Session = Depends(get_session),
):
    """
    Triggers profiling for all registered tables.
    Ideal for Airflow DAGs to keep profiles up-to-date.
    """
    try:
        await Client.connect(settings.TEMPORAL_HOST)
    except Exception as e:
        logger.error("[Profiling] Temporal not available: %s", e)
        raise HTTPException(status_code=503, detail="Temporal workflow engine is unavailable.")

    tables = session.exec(select(Table)).all()
    count = 0
    for table in tables:
        background_tasks.add_task(trigger_temporal_profiling_workflow, table.id)
        count += 1

    logger.info(
        f"[Profiling] Queued profiling job for {count} out of {len(tables)} tables."
    )
    return {
        "message": f"Queued profiling for {count} tables.",
        "total_tables": len(tables),
        "queued": count,
    }


# ── POST /tables/{id}/profile/run ─────────────────────────────────────────────
@router.post(
    "/tables/{table_id}/profile/run", response_model=TableProfileRead, status_code=202
)
async def run_table_profile(
    table_id: str,
    background_tasks: BackgroundTasks,
    force: bool = False,
    resume_from_partial: bool = False,
    session: Session = Depends(get_session),
):
    """
    Trigger a new profiling run against Trino.
    Returns immediately (202) while profiling runs in the background.
    Respects a 24h cache unless force=True.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    run = ProfilingRun(table_id=table_id, status=ProfilingStatus.pending)
    session.add(run)
    session.commit()
    session.refresh(run)

    success = await trigger_temporal_profiling_workflow(table_id, resume_from_partial)
    if not success:
        run.status = ProfilingStatus.failed
        run.error_message = "Temporal workflow engine is unavailable."
        session.add(run)
        session.commit()
        raise HTTPException(status_code=503, detail="Temporal workflow engine is unavailable.")

    logger.info(f"[Profiling] Queued profiling job: table={table_id}")

    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    profile_dict = profile.model_dump() if profile else {
        "id": "pending",
        "table_id": table_id,
        "row_count": None,
        "sample_size": None,
        "column_count": None,
        "size_bytes": None,
        "null_rate_avg": None,
        "duplicate_rate": None,
        "sample_data": None,
        "profile_json": None,
        "cached_until": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    return TableProfileRead(
        **profile_dict,
        status=ProfilingStatus.pending,
        is_partial=False
    )


# ── POST /tables/{id}/profile/terminate ───────────────────────────────────────
@router.post("/tables/{table_id}/profile/terminate", status_code=200)
async def terminate_table_profile(table_id: str, session: Session = Depends(get_session)):
    """
    Terminates a running Temporal profiling workflow for the given table.
    """
    table = session.get(Table, table_id)
    latest_run = session.exec(
        select(ProfilingRun).where(ProfilingRun.table_id == table_id).order_by(ProfilingRun.started_at.desc())
    ).first()

    if not table or not latest_run or latest_run.status not in (ProfilingStatus.running, ProfilingStatus.pending):
        raise HTTPException(status_code=404, detail="Table or active run not found")

    try:
        client = await Client.connect(settings.TEMPORAL_HOST)
        handle = client.get_workflow_handle(f"profile-{table_id}")
        await handle.cancel()
    except Exception as e:
        logger.warning(f"Failed to cancel temporal workflow for {table_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel temporal workflow: {e}")

    latest_run.status = ProfilingStatus.canceled
    session.add(latest_run)
    session.commit()

    return {"message": "Profiling job terminated", "status": "canceled"}


# ── GET /tables/{id}/profile/columns ──────────────────────────────────────────
@router.get(
    "/tables/{table_id}/profile/columns", response_model=list[ColumnProfileRead]
)
def get_column_profiles(table_id: str, session: Session = Depends(get_session)):
    profile = session.exec(
        select(TableProfile).where(
            TableProfile.table_id == table_id,
        )
    ).first()
    if not profile:
        return []
    return session.exec(
        select(ColumnProfile).where(ColumnProfile.profile_id == profile.id)
    ).all()


# ── GET /tables/{id}/columns/{col}/profile ────────────────────────────────────
@router.get(
    "/tables/{table_id}/columns/{column}/profile", response_model=ColumnProfileRead
)
def get_single_column_profile(
    table_id: str, column: str, session: Session = Depends(get_session)
):
    profile = session.exec(
        select(TableProfile).where(
            TableProfile.table_id == table_id,
        )
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No completed profile found")
    cp = session.exec(
        select(ColumnProfile).where(
            ColumnProfile.profile_id == profile.id, ColumnProfile.column_name == column
        )
    ).first()
    if not cp:
        raise HTTPException(status_code=404, detail=f"Column '{column}' not profiled")
    return cp


# ── GET /tables/{id}/profile/context (LLM context injection) ──────────────────
@router.get("/tables/{table_id}/profile/context")
def get_profile_context(
    table_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """
    Returns a compact, LLM-ready context blob built from the latest
    completed profile. Used by the TextToSQL context builder for
    system-prompt injection, enrichment suggestions, and join suggestions.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    profile = session.exec(
        select(TableProfile).where(
            TableProfile.table_id == table_id,
        )
    ).first()
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="No completed profile. Run POST /tables/{id}/profile/run first.",
        )

    col_profiles = session.exec(
        select(ColumnProfile).where(ColumnProfile.profile_id == profile.id)
    ).all()

    context = build_context_for_llm(
        table_name=f"{table.schema_name}.{table.name}",
        profile_json=profile.profile_json or {},
        column_profiles=col_profiles,
    )
    context["profile_id"] = profile.id
    context["computed_at"] = profile.created_at.isoformat()
    return context


# ── POST /tables/{id}/cross-profile ───────────────────────────────────────────
@router.post(
    "/tables/{table_id}/cross-profile",
    response_model=list[CrossTableProfileRead],
    status_code=201,
)
def cross_profile(table_id: str, session: Session = Depends(get_session)):
    """Discover cross-table join suggestions based on profiling statistics."""
    return discover_joins_for_table(table_id)


# ── GET /tables/{id}/cross-profile ────────────────────────────────────────────
@router.get(
    "/tables/{table_id}/cross-profile", response_model=list[CrossTableProfileRead]
)
def get_cross_profiles(table_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(CrossTableProfile).where(CrossTableProfile.source_table_id == table_id)
    ).all()
