#!/usr/bin/env python3
"""
trigger_profiling.py
---------------------
Directly triggers profiling for the first N registered tables
by calling the profiling engine + persisting results — no HTTP auth needed.

Usage (from project root inside backend container or with PYTHONPATH set):
    docker exec text2sql-onboarding-backend-1 python /app/scripts/trigger_profiling.py
"""
import sys
import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("trigger_profiling")

sys.path.insert(0, "/app/backend")


from sqlmodel import Session, select
from core.db.engine import engine
from core.models.models import Table, TableProfile, ColumnProfile, ProfilingStatus
from core.services.profiling_engine import run_table_profiling

MAX_TABLES = 25  # Profile at most this many tables


def run():
    with Session(engine) as session:
        tables = session.exec(select(Table)).all()

    logger.info(f"Found {len(tables)} registered tables. Will profile up to {MAX_TABLES}.")
    targets = tables[:MAX_TABLES]

    for i, table in enumerate(targets, 1):
        table_id = str(table.id)
        logger.info(
            f"[{i}/{len(targets)}] Profiling: {table.catalog}.{table.schema_name}.{table.name}"
        )

        # Create a TableProfile record in 'running' state
        with Session(engine) as session:
            # Determine version
            latest = session.exec(
                select(TableProfile)
                .where(TableProfile.table_id == table_id)
                .order_by(TableProfile.version.desc())
            ).first()
            version = (latest.version if latest else 0) + 1

            profile = TableProfile(
                table_id=table_id,
                status=ProfilingStatus.running,
                version=version,
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            profile_id = profile.id

        # Run the profiling engine (synchronously)
        try:
            result = run_table_profiling(
                table_id=table_id,
                catalog=table.catalog,
                schema=table.schema_name,
                table=table.name,
                version=version,
            )
        except Exception as e:
            logger.error(f"  ✗ Engine failed: {e}")
            with Session(engine) as session:
                p = session.get(TableProfile, profile_id)
                if p:
                    p.status = ProfilingStatus.failed
                    p.updated_at = datetime.utcnow()
                    session.add(p)
                    session.commit()
            continue

        # Persist results
        with Session(engine) as session:
            p = session.get(TableProfile, profile_id)
            if not p:
                continue

            p.status = ProfilingStatus.completed if result.success else ProfilingStatus.failed
            p.version = result.version
            p.row_count = result.row_count
            p.sample_size = result.sample_size
            p.column_count = result.column_count
            p.null_rate_avg = result.null_rate_avg
            p.sample_data = result.sample_data
            p.profile_json = result.profile_json
            p.cached_until = datetime.utcnow() + timedelta(hours=24)
            p.updated_at = datetime.utcnow()
            session.add(p)

            for cs in result.column_stats:
                cp = ColumnProfile(
                    table_id=table_id,
                    profile_id=profile_id,
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

        status_icon = "✓" if result.success else "✗"
        logger.info(
            f"  {status_icon} Done: {result.row_count} rows, "
            f"{result.column_count} columns, {len(result.column_stats)} column profiles"
        )

    logger.info("\nAll profiling jobs complete.")


if __name__ == "__main__":
    run()
