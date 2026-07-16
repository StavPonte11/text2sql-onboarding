import dataclasses
import logging
from datetime import datetime, timedelta, timezone

from typing import Any, Dict, List, Optional
from core.db.engine import engine
from core.models.models import ColumnProfile, ProfilingStatus, Table, TableProfile
from sqlmodel import Session, select
from temporalio import activity

from core.services.profiling_engine import (
    ColumnStats,
    TableProfilingResult,
    _analyze_column,
    _fetch_all,
    _fetch_one,
    _fqn,
    _make_json_safe,
    build_column_metadata_query,
    build_combined_profiling_query,
    build_row_count_query,
    build_sample_query,
    execute_query_sync,
    execute_with_timeout,
    generate_table_summary,
    parse_combined_result,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ChunkMetricsParams:
    fqn: str
    table_id: str
    row_count: int
    columns_chunk: List[Any]

@dataclasses.dataclass
class ProfileColumnParams:
    col_name: str
    data_type: str
    row_count: int
    catalog: str
    schema_name: str
    table_name: str
    table_id: str
    precomputed: Optional[Dict[str, Any]] = None
    sample_data: Optional[List[Dict[str, Any]]] = None

@dataclasses.dataclass
class AiSummaryParams:
    table_id: str
    table_fqn: str
    row_count: int
    column_count: int
    column_stats: List[Dict[str, Any]]

@dataclasses.dataclass
class PersistResultsParams:
    table_id: str
    profile_id: str
    table_fqn: str
    row_count: int
    sample_size: int
    column_count: int
    sample_data: List[Dict[str, Any]]
    column_stats: List[Dict[str, Any]]
    ai_summary: str = ""
    is_partial: bool = False
    failed_subtasks: List[str] = dataclasses.field(default_factory=list)
    errors: List[str] = dataclasses.field(default_factory=list)


@activity.defn
def fetch_table_metadata_activity(table_id: str, resume_from_partial: bool = False) -> dict:
    logger.info("Starting fetch_table_metadata_activity for table_id: %s (resume=%s)", table_id, resume_from_partial)
    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            raise ValueError(f"Table with ID {table_id} not found")

        catalog = table.catalog
        schema_name = table.schema_name
        table_name = table.name
        fqn = _fqn(catalog, schema_name, table_name)

        profile = session.exec(
            select(TableProfile).where(TableProfile.table_id == table_id)
        ).first()
        if not profile:
            profile = TableProfile(table_id=table_id)

        profile.status = ProfilingStatus.running
        profile.updated_at = datetime.now(timezone.utc)
        session.add(profile)
        session.commit()
        session.refresh(profile)
        profile_id = profile.id

    row_count_res = _fetch_one(build_row_count_query(fqn), table_id, default=(0,))
    row_count = int(row_count_res[0] or 0)

    columns_meta = _fetch_all(build_column_metadata_query(catalog, schema_name, table_name), table_id)

    sample_data = []
    sample_size = 0
    sample_res = execute_query_sync(build_sample_query(fqn), table_id)
    if sample_res.success and sample_res.rows:
        sample_size = len(sample_res.rows)
        sample_data = [_make_json_safe(dict(zip(sample_res.columns, row, strict=False))) for row in sample_res.rows[:50]]

    existing_columns = []
    if resume_from_partial:
        with Session(engine) as session:
            existing_cols_db = session.exec(
                select(ColumnProfile).where(ColumnProfile.table_id == table_id)
            ).all()
            for col in existing_cols_db:
                existing_columns.append({
                    "column_name": col.column_name,
                    "data_type": col.data_type,
                    "null_count": col.null_count,
                    "null_rate": col.null_rate,
                    "distinct_count": col.distinct_count,
                    "min_value": col.min_value,
                    "max_value": col.max_value,
                    "avg_value": col.avg_value,
                    "median_value": col.median_value,
                    "top_values": col.top_values,
                    "is_categorical": col.is_categorical,
                    "is_geo": col.is_geo,
                    "is_time": col.is_time,
                    "semantic_type": col.semantic_type,
                    "stats_json": col.stats_json,
                    "errors": [],
                })

    return {
        "table_id": table_id,
        "profile_id": profile_id,
        "catalog": catalog,
        "schema_name": schema_name,
        "table_name": table_name,
        "fqn": fqn,
        "row_count": row_count,
        "columns_meta": columns_meta,
        "sample_data": sample_data,
        "sample_size": sample_size,
        "existing_columns": existing_columns,
    }


@activity.defn
def compute_chunk_metrics_activity(params: ChunkMetricsParams) -> dict:
    fqn = params.fqn
    table_id = params.table_id
    row_count = params.row_count
    columns_chunk = params.columns_chunk

    if row_count <= 0 or not columns_chunk:
        return {}

    combined_query = build_combined_profiling_query(fqn, columns_chunk)
    if not combined_query:
        return {}

    logger.info("Executing chunk profiling query for table %s (%d columns)", fqn, len(columns_chunk))
    res = execute_with_timeout(combined_query, table_id)
    if res.success and res.rows:
        row_dict = dict(zip(res.columns, res.rows[0]))
        return parse_combined_result(columns_chunk, row_dict, row_count)
    else:
        logger.warning("Chunk profiling query unsuccessful: %s", res.error_message)
        return {}


@activity.defn
def profile_column_activity(params: ProfileColumnParams) -> dict:
    col_name = params.col_name
    data_type = params.data_type
    row_count = params.row_count
    precomputed = params.precomputed
    sample_data = params.sample_data
    catalog = params.catalog
    schema = params.schema_name
    table = params.table_name
    table_id = params.table_id

    fqn = _fqn(catalog, schema, table)
    logger.info("Profiling column %s (%s)", col_name, data_type)
    stats = _analyze_column(fqn, table_id, col_name, data_type, row_count, precomputed, sample_data)

    # Convert dataclass to dict
    return dataclasses.asdict(stats)


@activity.defn
def generate_ai_summary_activity(params: AiSummaryParams) -> str:
    table_fqn = params.table_fqn
    row_count = params.row_count
    column_count = params.column_count
    column_stats_dicts = params.column_stats

    # Re-construct ColumnStats objects for generate_table_summary
    col_stats = []
    for cs in column_stats_dicts:
        # Filter fields to match dataclass
        fields = {f.name: cs[f.name] for f in dataclasses.fields(ColumnStats) if f.name in cs}
        col_stats.append(ColumnStats(**fields))

    # Re-construct TableProfilingResult
    result = TableProfilingResult(
        table_id=params.table_id,
        table_fqn=table_fqn,
        version=1,
        computed_at=datetime.now(timezone.utc),
        row_count=row_count,
        column_count=column_count,
        column_stats=col_stats,
    )

    logger.info("Generating AI summary for table %s", table_fqn)
    return generate_table_summary(result)


@activity.defn
def persist_profiling_results_activity(params: PersistResultsParams) -> None:
    table_id = params.table_id
    profile_id = params.profile_id
    row_count = params.row_count
    sample_size = params.sample_size
    column_count = params.column_count
    sample_data = params.sample_data
    column_stats_dicts = params.column_stats
    ai_summary = params.ai_summary
    is_partial = params.is_partial
    errors = params.errors

    logger.info("Persisting profiling results for table_id: %s (is_partial=%s)", table_id, is_partial)

    col_stats = []
    for cs in column_stats_dicts:
        fields = {f.name: cs[f.name] for f in dataclasses.fields(ColumnStats) if f.name in cs}
        col_stats.append(ColumnStats(**fields))

    # Reconstruct profile_json
    insights = [f"~{row_count:,} rows (COUNT(*))."] if row_count else []
    if sample_size:
        insights.append(f"{sample_size:,} rows sampled.")

    for flag, name in [("is_categorical", "categorical"), ("is_time", "Time"), ("is_geo", "Geographic")]:
        cols = [c.column_name for c in col_stats if getattr(c, flag)]
        if cols:
            insights.append(f"{name} columns: {', '.join(cols[:5])}.")

    high_null = [c.column_name for c in col_stats if c.null_rate > 0.20]
    if high_null:
        insights.append(f"High null rate (>20%): {', '.join(high_null[:5])}.")

    if row_count > 0:
        pk_candidates = [c.column_name for c in col_stats if c.distinct_count >= row_count * 0.95]
        if pk_candidates:
            insights.append(f"PK candidates: {', '.join(pk_candidates[:3])}.")

    null_rate_avg = round(sum(c.null_rate for c in col_stats) / len(col_stats), 4) if col_stats else 0.0

    profile_json = _make_json_safe({
        "table": params.table_fqn,
        "version": 1,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count,
        "sample_size": sample_size,
        "column_count": column_count,
        "null_rate_avg": null_rate_avg,
        "columns": [{
            "name": c.column_name, "data_type": c.data_type, "semantic_type": c.semantic_type,
            "is_categorical": c.is_categorical, "is_large_categorical": c.is_large_categorical,
            "is_free_text": c.is_free_text, "is_boolean": c.is_boolean, "is_time": c.is_time,
            "is_geo": c.is_geo, "is_continuous": c.is_continuous, "distinct_count": c.distinct_count,
            "null_rate": c.null_rate, "stats": c.stats_json
        } for c in col_stats],
        "insights": insights,
        "errors": errors,
        "failed_subtasks": params.failed_subtasks,
    })

    with Session(engine) as session:
        profile = session.get(TableProfile, profile_id)
        if not profile:
            profile = TableProfile(id=profile_id, table_id=table_id)
            session.add(profile)

        profile.status = ProfilingStatus.completed
        profile.is_partial = is_partial
        profile.row_count = row_count
        profile.sample_size = sample_size
        profile.column_count = column_count
        profile.null_rate_avg = null_rate_avg
        profile.auto_insights = insights
        profile.sample_data = sample_data
        profile.profile_json = profile_json
        profile.cached_until = datetime.now(timezone.utc) + timedelta(hours=24)
        profile.updated_at = datetime.now(timezone.utc)
        session.add(profile)
        session.commit()

        # Clear old column profiles
        old_cols = session.exec(
            select(ColumnProfile).where(ColumnProfile.table_id == table_id)
        ).all()
        for old_c in old_cols:
            session.delete(old_c)
        session.commit()

        # Persist new column profiles
        for cs in col_stats:
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
                stats_json=_make_json_safe(cs.stats_json),
            )
            session.add(cp)
        session.commit()

    if ai_summary:
        try:
            from app.routers.profiling import _upsert_ai_summary
            with Session(engine) as session:
                _upsert_ai_summary(session, table_id, ai_summary)
        except Exception as exc:
            logger.warning("[Profiling] AI summary persist step failed: %s", exc)
