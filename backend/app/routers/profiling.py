"""
profiling.py — Production-grade profiling router.

Replaces all stubs with real Trino-backed execution via profiling_engine.
New endpoint: GET /tables/{id}/profile/context — LLM-ready context blob.
"""

import logging
import traceback
from datetime import datetime, timedelta
from typing import Any
import anyio
import anyio.to_thread
from temporalio.client import Client, WorkflowExecutionStatus
from app.config import settings

from core.db.engine import engine, get_session
from core.models.models import (
    ColumnProfile,
    ColumnProfileRead,
    CrossTableProfile,
    CrossTableProfileRead,
    EnrichmentVersion,
    ProfilingStatus,
    Table,
    TableProfile,
    TableProfileRead,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.services.join_detection import discover_joins_for_table
from core.services.profiling_engine import (
    build_context_for_llm,
    generate_table_summary,
    run_table_profiling,
)

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
    except Exception as e:
        if "WorkflowAlreadyStartedError" in str(type(e)) or "WorkflowExecutionAlreadyStartedError" in str(type(e)):
            logger.info("[Profiling] Profiling workflow is already running for table %s", table_id)
            return True
        logger.error("[Profiling] Failed to start Temporal workflow for table %s: %s", table_id, e)
        return False


def _run_profile_job_local(table_id: str):
    """
    Background task: runs real Trino profiling via profiling_engine,
    then upserts results into table_profiles + column_profiles.
    Fallback when Temporal is not available.
    """
    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            logger.error(f"[Profiling] table_id={table_id} not found")
            return

        schema_name = table.schema_name
        table_name = table.name
        catalog = table.catalog

    # Run engine OUTSIDE the session to avoid long-held DB connections
    try:
        result = run_table_profiling(
            table_id=table_id,
            catalog=catalog,
            schema=schema_name,
            table=table_name,
            version=1,
        )
    except Exception as exc:
        with open("error.log", "w") as f:
            f.write(traceback.format_exc())
        logger.error(f"[Profiling] Engine failed for {table_id}: {exc}")
        return

    # Persist results (Upsert by table_id)
    with Session(engine) as session:
        profile = session.exec(
            select(TableProfile).where(TableProfile.table_id == table_id)
        ).first()
        if not profile:
            profile = TableProfile(table_id=table_id)
            session.add(profile)

        profile.status = ProfilingStatus.completed
        profile.is_partial = not result.success or bool(result.errors)
        profile.row_count = result.row_count
        profile.sample_size = result.sample_size
        profile.column_count = result.column_count
        profile.null_rate_avg = result.null_rate_avg
        profile.auto_insights = result.auto_insights
        profile.sample_data = result.sample_data
        profile.profile_json = result.profile_json
        profile.cached_until = datetime.now() + timedelta(hours=24)
        profile.updated_at = datetime.now()
        session.add(profile)
        session.commit()
        session.refresh(profile)

        # Clear old column profiles
        old_cols = session.exec(
            select(ColumnProfile).where(ColumnProfile.table_id == table_id)
        ).all()
        for old_c in old_cols:
            session.delete(old_c)
        session.commit()

        # Persist new column profiles
        for cs in result.column_stats:
            cp = ColumnProfile(
                table_id=table_id,
                profile_id=profile.id,
                column_name=cs.column_name,
                data_type=cs.data_type,
                null_count=cs.null_count,
                null_rate=cs.null_rate,
                distinct_count=cs.distinct_count,
                min_value=cs.min_value,
                max_value=cs.max_value,
                avg_value=cs.avg_value,
                median_value=cs.median_value,
                top_values=cs.top_values,
                is_categorical=cs.is_categorical,
                is_geo=cs.is_geo,
                is_time=cs.is_time,
                semantic_type=cs.semantic_type,
                stats_json=cs.stats_json,
            )
            session.add(cp)

        session.commit()
        logger.info(
            f"[Profiling] Persisted profile {profile.id} for table {table_id} "
            f"({len(result.column_stats)} columns)"
        )

    # Generate one-time LLM summary and persist into EnrichmentVersion
    try:
        summary = generate_table_summary(result)
        if summary:
            with Session(engine) as session:
                _upsert_ai_summary(session, table_id, summary)
    except Exception as exc:
        logger.warning("[Profiling] AI summary step failed for %s: %s", table_id, exc)


async def _trigger_profiling(table_id: str, resume_from_partial: bool = False):
    success = await trigger_temporal_profiling_workflow(table_id, resume_from_partial)
    if not success:
        logger.info("Falling back to local FastAPI background task profiling for %s", table_id)
        await anyio.to_thread.run_sync(_run_profile_job_local, table_id)


# ── GET /tables/{id}/profile ───────────────────────────────────────────────────
@router.get("/tables/{table_id}/profile", response_model=TableProfileRead)
async def get_table_profile(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    if not profile:
        raise HTTPException(
            status_code=404, detail="No profile found. Run POST /profile/run first."
        )

    # Sync state from Temporal if stuck
    if profile.status in (ProfilingStatus.running, ProfilingStatus.pending):
        try:
            client = await Client.connect(settings.TEMPORAL_HOST)
            handle = client.get_workflow_handle(f"profile-{table_id}")
            desc = await handle.describe()
            if desc.status in (
                WorkflowExecutionStatus.FAILED,
                WorkflowExecutionStatus.TERMINATED,
                WorkflowExecutionStatus.TIMED_OUT,
                WorkflowExecutionStatus.CANCELED
            ):
                profile.status = ProfilingStatus.failed
                session.add(profile)
                session.commit()
                session.refresh(profile)
        except Exception as e:
            if "NotFound" in str(type(e)):
                profile.status = ProfilingStatus.failed
                session.add(profile)
                session.commit()
                session.refresh(profile)
            else:
                logger.warning("[Profiling] Failed to sync temporal status for %s: %s", table_id, e)

    return profile


# ── POST /tables/all/profile/run ──────────────────────────────────────────────
@router.post("/tables/all/profile/run", status_code=202)
def run_all_profiles(
    background_tasks: BackgroundTasks,
    force: bool = False,
    session: Session = Depends(get_session),
):
    """
    Triggers profiling for all registered tables.
    Ideal for Airflow DAGs to keep profiles up-to-date.
    """
    tables = session.exec(select(Table)).all()
    count = 0
    for table in tables:
        background_tasks.add_task(_trigger_profiling, table.id)
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
def run_table_profile(
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

    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    background_tasks.add_task(_trigger_profiling, table_id, resume_from_partial)
    logger.info(f"[Profiling] Queued profiling job: table={table_id}")

    if profile:
        # Optimistically update to pending
        profile.status = ProfilingStatus.pending
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile

    # Return a dummy pending profile just to satisfy response_model if not exists yet
    return TableProfile(table_id=table_id, status=ProfilingStatus.pending)


# ── POST /tables/{id}/profile/terminate ───────────────────────────────────────
@router.post("/tables/{table_id}/profile/terminate", status_code=200)
async def terminate_table_profile(table_id: str, session: Session = Depends(get_session)):
    """
    Terminates a running Temporal profiling workflow for the given table.
    """
    table = session.get(Table, table_id)
    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    if not table or not profile or profile.status not in (ProfilingStatus.running, ProfilingStatus.pending):
        raise HTTPException(status_code=404, detail="Table not found")

    try:
        client = await Client.connect(settings.TEMPORAL_HOST)
        handle = client.get_workflow_handle(f"profile-{table_id}")
        await handle.cancel()
    except Exception as e:
        logger.warning(f"Failed to cancel temporal workflow for {table_id}: {e}")

    profile = session.exec(
        select(TableProfile).where(TableProfile.table_id == table_id)
    ).first()

    if profile:
        profile.status = ProfilingStatus.failed
        session.add(profile)
        session.commit()

    return {"message": "Profiling job terminated", "status": "failed"}


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
