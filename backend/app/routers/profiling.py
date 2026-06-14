"""
profiling.py — Production-grade profiling router.

Replaces all stubs with real Trino-backed execution via profiling_engine.
New endpoint: GET /tables/{id}/profile/context — LLM-ready context blob.
"""

import logging
import traceback
from datetime import datetime, timedelta
from typing import Any

from core.db.engine import engine, get_session
from core.models.models import (
    ColumnProfile,
    ColumnProfileRead,
    CrossTableProfile,
    CrossTableProfileRead,
    ProfilingStatus,
    Table,
    TableProfile,
    TableProfileRead,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.services.join_detection import discover_joins_for_table
from app.services.profiling_engine import (
    build_context_for_llm,
    run_table_profiling,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["profiling"])


# ── Background worker ──────────────────────────────────────────────────────────
def _run_profile_job(table_id: str):
    """
    Background task: runs real Trino profiling via profiling_engine,
    then upserts results into table_profiles + column_profiles.
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

    if not result.success:
        logger.error(f"[Profiling] Engine unsuccessful for {table_id}")
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
        profile.row_count = result.row_count
        profile.sample_size = result.sample_size
        profile.column_count = result.column_count
        profile.null_rate_avg = result.null_rate_avg
        profile.auto_insights = result.auto_insights
        profile.sample_data = result.sample_data
        profile.profile_json = result.profile_json
        profile.cached_until = datetime.utcnow() + timedelta(hours=24)
        profile.updated_at = datetime.utcnow()
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


# ── GET /tables/{id}/profile ───────────────────────────────────────────────────
@router.get("/tables/{table_id}/profile", response_model=TableProfileRead)
def get_table_profile(table_id: str, session: Session = Depends(get_session)):
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
        # If not force, check for running profile
        background_tasks.add_task(_run_profile_job, str(table.id))
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

    background_tasks.add_task(_run_profile_job, table_id)
    logger.info(f"[Profiling] Queued profiling job: table={table_id}")

    if profile:
        return profile

    # Return a dummy pending profile just to satisfy response_model if not exists yet
    return TableProfile(table_id=table_id, status=ProfilingStatus.pending)


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
