import asyncio
from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table
from app.config import settings
from app.services.profiling_engine import run_table_profiling
from app.services.join_detection import discover_joins_for_table

def test_flow():
    with Session(engine) as session:
        tables = session.exec(select(Table)).all()
        if not tables:
            print("No tables found")
            return
            
        for t in tables:
            print(f"Profiling {t.name}...")
            res = run_table_profiling(t.id, settings.TRINO_CATALOG, t.schema_name, t.name)
            if not res.success:
                print(f"Failed to profile {t.name}: {res.errors}")
            else:
                print(f"Success! Found {res.row_count} rows, {res.column_count} columns.")
                
            # persist profile (profiling_engine.py doesn't persist, profiling.py does)
            from app.models.models import TableProfile, ColumnProfile, ProfilingStatus
            from datetime import datetime
            profile = TableProfile(
                table_id=t.id,
                status=ProfilingStatus.completed,
                version=res.version,
                row_count=res.row_count,
                sample_size=res.sample_size,
                column_count=res.column_count,
                null_rate_avg=res.null_rate_avg,
                auto_insights=res.auto_insights,
                sample_data=res.sample_data,
                profile_json=res.profile_json,
                updated_at=datetime.utcnow()
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            
            for cs in res.column_stats:
                cp = ColumnProfile(
                    table_id=t.id,
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

        # Run join detection
        for t in tables:
            print(f"Discovering joins for {t.name}...")
            joins = discover_joins_for_table(t.id)
            for j in joins:
                print(f"Found join: {j.join_suggestion} (strength: {j.match_strength})")

if __name__ == "__main__":
    test_flow()
