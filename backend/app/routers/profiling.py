"""
Profiling router — handles table profiling jobs, column profiles, and cross-table analysis.
AI/Trino execution is stubbed; the router handles persistence and async orchestration only.
"""
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from app.db.engine import get_session, engine
from app.models.models import (
    Table, TableProfile, TableProfileRead,
    ColumnProfile, ColumnProfileRead,
    CrossTableProfile, CrossTableProfileRead,
    ProfilingStatus,
)

router = APIRouter(tags=["profiling"])


def _run_profile_job(table_id: str, profile_id: str):
    """
    Background worker. In production this calls Trino via sampling queries.
    Here we populate realistic stub values so the UI renders correctly.
    """
    import time
    import random
    time.sleep(3)  # simulate Trino latency

    with Session(engine) as session:
        profile = session.get(TableProfile, profile_id)
        if not profile:
            return

        profile.status = ProfilingStatus.completed
        profile.row_count = random.randint(10_000, 5_000_000)
        profile.column_count = random.randint(5, 40)
        profile.null_rate_avg = round(random.uniform(0.01, 0.25), 3)
        profile.duplicate_rate = round(random.uniform(0.0, 0.05), 3)
        profile.auto_insights = [
            "Column 'event_date' has no nulls — suitable as partition key.",
            "Column 'user_id' has 98% distinct values — likely a primary key candidate.",
            f"~{profile.row_count:,} rows sampled (APPROX_DISTINCT applied).",
        ]
        profile.sample_data = [
            {"id": i, "user_id": f"u-{random.randint(1000,9999)}", "value": round(random.uniform(0, 100), 2)}
            for i in range(1, 6)
        ]
        profile.cached_until = datetime.utcnow() + timedelta(hours=24)
        profile.updated_at = datetime.utcnow()
        session.add(profile)

        # Stub column profiles
        col_names = [f"col_{i}" for i in range(1, (profile.column_count or 5) + 1)]
        type_pool = ["VARCHAR", "BIGINT", "DOUBLE", "DATE", "BOOLEAN", "TIMESTAMP"]
        for col in col_names:
            cp = ColumnProfile(
                table_id=table_id,
                profile_id=profile_id,
                column_name=col,
                data_type=random.choice(type_pool),
                null_count=random.randint(0, 5000),
                null_rate=round(random.uniform(0.0, 0.3), 3),
                distinct_count=random.randint(2, 100_000),
                min_value=str(random.randint(0, 100)),
                max_value=str(random.randint(100, 10_000)),
                avg_value=round(random.uniform(0, 1000), 2),
                top_values=[
                    {"value": f"val_{j}", "count": random.randint(100, 50_000)}
                    for j in range(1, 6)
                ],
                is_geo=col in ["lat", "lon", "geometry", "location"],
                is_time=col in ["event_date", "created_at", "timestamp"],
            )
            session.add(cp)

        session.commit()


# ── GET latest profile ─────────────────────────────────────────────────────────
@router.get("/tables/{table_id}/profile", response_model=TableProfileRead)
def get_table_profile(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    profile = session.exec(
        select(TableProfile)
        .where(TableProfile.table_id == table_id)
        .order_by(TableProfile.created_at.desc())
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="No profile found. Run a profiling job first.")
    return profile


# ── POST trigger new profiling run ────────────────────────────────────────────
@router.post("/tables/{table_id}/profile/run", response_model=TableProfileRead, status_code=202)
def run_table_profile(
    table_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    # Check if a cached profile is still valid
    existing = session.exec(
        select(TableProfile)
        .where(TableProfile.table_id == table_id)
        .order_by(TableProfile.created_at.desc())
    ).first()
    if existing and existing.cached_until and existing.cached_until > datetime.utcnow():
        return existing  # serve from cache

    profile = TableProfile(table_id=table_id, status=ProfilingStatus.running)
    session.add(profile)
    session.commit()
    session.refresh(profile)

    background_tasks.add_task(_run_profile_job, table_id, profile.id)
    return profile


# ── GET column profiles for a table ──────────────────────────────────────────
@router.get("/tables/{table_id}/profile/columns", response_model=list[ColumnProfileRead])
def get_column_profiles(table_id: str, session: Session = Depends(get_session)):
    # Get latest completed profile
    profile = session.exec(
        select(TableProfile)
        .where(TableProfile.table_id == table_id, TableProfile.status == ProfilingStatus.completed)
        .order_by(TableProfile.created_at.desc())
    ).first()
    if not profile:
        return []
    return session.exec(
        select(ColumnProfile).where(ColumnProfile.profile_id == profile.id)
    ).all()


# ── GET column-level profile ──────────────────────────────────────────────────
@router.get("/tables/{table_id}/columns/{column}/profile", response_model=ColumnProfileRead)
def get_single_column_profile(table_id: str, column: str, session: Session = Depends(get_session)):
    profile = session.exec(
        select(TableProfile)
        .where(TableProfile.table_id == table_id, TableProfile.status == ProfilingStatus.completed)
        .order_by(TableProfile.created_at.desc())
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No completed profile found")
    cp = session.exec(
        select(ColumnProfile)
        .where(ColumnProfile.profile_id == profile.id, ColumnProfile.column_name == column)
    ).first()
    if not cp:
        raise HTTPException(status_code=404, detail="Column profile not found")
    return cp


# ── POST cross-table analysis ─────────────────────────────────────────────────
@router.post("/tables/{table_id}/cross-profile", response_model=list[CrossTableProfileRead], status_code=201)
def cross_profile(table_id: str, session: Session = Depends(get_session)):
    """
    Stub: in production this calls the metadata discovery service to find join candidates.
    Returns existing cross-profiles for this table (or empty list).
    """
    results = session.exec(
        select(CrossTableProfile).where(CrossTableProfile.source_table_id == table_id)
    ).all()
    return results


# ── GET cross-table profiles ──────────────────────────────────────────────────
@router.get("/tables/{table_id}/cross-profile", response_model=list[CrossTableProfileRead])
def get_cross_profiles(table_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(CrossTableProfile).where(CrossTableProfile.source_table_id == table_id)
    ).all()
