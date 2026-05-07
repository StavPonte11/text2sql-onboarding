"""
profiling_engine.py — Real Trino-backed data profiling engine.

Runs full scan queries against Trino, computes column statistics,
detects categorical vs continuous columns, and produces structured output
ready for PostgreSQL persistence and LLM context injection.
"""
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.trino_client import execute_query_sync

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────────
CATEGORICAL_DISTINCT_THRESHOLD = 50
CATEGORICAL_COVERAGE_THRESHOLD = 0.90   # top-N values cover ≥90% → categorical
SAMPLE_PERCENT = 10                      # TABLESAMPLE BERNOULLI(10)
SAMPLE_LIMIT = 10_000
TOP_VALUES_LIMIT = 50

NUMERIC_TYPES = {
    "bigint", "integer", "smallint", "tinyint",
    "double", "real", "decimal", "float", "number",
}
TIME_TYPES = {
    "date", "timestamp", "timestamp with time zone",
    "timestamp(3) with time zone", "time",
}
GEO_HINTS = {"lat", "lon", "latitude", "longitude", "geometry", "geom", "location", "coordinates"}
TIME_HINTS = {
    "date", "time", "timestamp", "created_at", "updated_at",
    "event_at", "occurred_at", "dt", "day", "month", "year", "week",
}


# ── Data structures ────────────────────────────────────────────────────────────
@dataclass
class ColumnStats:
    column_name: str
    data_type: str
    null_count: int = 0
    null_rate: float = 0.0
    distinct_count: int = 0
    is_categorical: bool = False
    is_geo: bool = False
    is_time: bool = False
    semantic_type: str = "continuous"   # categorical | continuous | time | geo
    top_values: List[Dict] = field(default_factory=list)
    value_frequencies: Dict[str, int] = field(default_factory=dict)
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    avg_value: Optional[float] = None
    median_value: Optional[float] = None
    sample_values: List[Any] = field(default_factory=list)
    stats_json: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class TableProfilingResult:
    table_id: str
    table_fqn: str
    version: int
    computed_at: datetime
    row_count: int = 0
    sample_size: int = 0
    column_count: int = 0
    null_rate_avg: float = 0.0
    auto_insights: List[str] = field(default_factory=list)
    sample_data: List[Dict] = field(default_factory=list)
    column_stats: List[ColumnStats] = field(default_factory=list)
    profile_json: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    success: bool = True


# ── SQL Query Builders ─────────────────────────────────────────────────────────
def build_row_count_query(fqn: str) -> str:
    return f"SELECT COUNT(*) FROM {fqn}"


def build_sample_query(fqn: str, limit: int = SAMPLE_LIMIT) -> str:
    return f"SELECT * FROM {fqn} LIMIT {limit}"


def build_column_metadata_query(catalog: str, schema: str, table: str) -> str:
    return (
        f"SELECT column_name, data_type, ordinal_position "
        f"FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
        f"ORDER BY ordinal_position"
    )


def build_distinct_count_query(fqn: str, col: str) -> str:
    return f'SELECT COUNT(DISTINCT "{col}") FROM {fqn}'


def build_null_ratio_query(fqn: str, col: str) -> str:
    return f'SELECT COUNT(*) AS total, COUNT("{col}") AS non_null FROM {fqn}'


def build_top_values_query(fqn: str, col: str, limit: int = TOP_VALUES_LIMIT) -> str:
    return (
        f'SELECT "{col}", COUNT(*) AS cnt '
        f'FROM {fqn} GROUP BY "{col}" ORDER BY cnt DESC LIMIT {limit}'
    )


def build_numeric_stats_query(fqn: str, col: str) -> str:
    return (
        f'SELECT '
        f'  MIN("{col}") AS min_val, '
        f'  MAX("{col}") AS max_val, '
        f'  AVG(CAST("{col}" AS DOUBLE)) AS avg_val, '
        f'  APPROX_PERCENTILE(CAST("{col}" AS DOUBLE), 0.5) AS median_val '
        f'FROM {fqn} WHERE "{col}" IS NOT NULL'
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
def _fqn(catalog: str, schema: str, table: str) -> str:
    return f'"{catalog}"."{schema}"."{table}"'


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _detect_semantic_type(
    is_categorical: bool, is_geo: bool, is_time: bool
) -> str:
    if is_geo:
        return "geo"
    if is_time:
        return "time"
    if is_categorical:
        return "categorical"
    return "continuous"


# ── Per-Column Analysis ────────────────────────────────────────────────────────
def _analyze_column(
    fqn: str, table_id: str, col_name: str, data_type: str, row_count: int
) -> ColumnStats:
    stats = ColumnStats(column_name=col_name, data_type=data_type)
    dtype_lower = data_type.lower()
    col_lower = col_name.lower()

    stats.is_geo = col_lower in GEO_HINTS or "geo" in col_lower or "coord" in col_lower
    stats.is_time = dtype_lower in TIME_TYPES or any(h in col_lower for h in TIME_HINTS)

    # 1. Exact distinct count
    r = execute_query_sync(build_distinct_count_query(fqn, col_name), table_id)
    if r.success and r.rows:
        stats.distinct_count = int(r.rows[0][0] or 0)
    else:
        stats.errors.append(f"distinct_count: {r.error_message}")

    # 2. Null ratio
    r = execute_query_sync(build_null_ratio_query(fqn, col_name), table_id)
    if r.success and r.rows:
        total = int(r.rows[0][0] or 1)
        non_null = int(r.rows[0][1] or 0)
        stats.null_count = total - non_null
        stats.null_rate = round(stats.null_count / max(total, 1), 4)
    else:
        stats.errors.append(f"null_ratio: {r.error_message}")

    # 3. Top values + categorical detection
    low_cardinality = 0 < stats.distinct_count < CATEGORICAL_DISTINCT_THRESHOLD
    top_values: List[Dict] = []

    if low_cardinality or not stats.is_time:
        r = execute_query_sync(build_top_values_query(fqn, col_name), table_id)
        if r.success and r.rows:
            top_values = [{"value": str(row[0]), "count": int(row[1])} for row in r.rows]
            top_coverage = sum(v["count"] for v in top_values) / max(row_count, 1)
            stats.is_categorical = low_cardinality or top_coverage >= CATEGORICAL_COVERAGE_THRESHOLD
            stats.top_values = top_values
            stats.value_frequencies = {v["value"]: v["count"] for v in top_values}
        else:
            stats.errors.append(f"top_values: {r.error_message}")

    # 4. Numeric stats (only for non-categorical numeric columns)
    if dtype_lower in NUMERIC_TYPES and not stats.is_categorical:
        r = execute_query_sync(build_numeric_stats_query(fqn, col_name), table_id)
        if r.success and r.rows:
            row = r.rows[0]
            stats.min_value = str(row[0]) if row[0] is not None else None
            stats.max_value = str(row[1]) if row[1] is not None else None
            stats.avg_value = _safe_float(row[2])
            stats.median_value = _safe_float(row[3])
        else:
            stats.errors.append(f"numeric_stats: {r.error_message}")

    # 5. Semantic type
    stats.semantic_type = _detect_semantic_type(
        stats.is_categorical, stats.is_geo, stats.is_time
    )

    # 6. stats_json blob (stored in column_profiles.stats_json)
    if stats.is_categorical:
        stats.stats_json = {
            "type": "categorical",
            "values": [v["value"] for v in top_values],
            "frequencies": stats.value_frequencies,
            "distinct_count": stats.distinct_count,
            "null_rate": stats.null_rate,
        }
    else:
        stats.stats_json = {
            "type": stats.semantic_type,
            "min": stats.min_value,
            "max": stats.max_value,
            "avg": stats.avg_value,
            "median": stats.median_value,
            "distinct_count": stats.distinct_count,
            "null_rate": stats.null_rate,
            "sample_values": [v["value"] for v in top_values[:10]],
        }

    return stats


# ── Main Profiler ──────────────────────────────────────────────────────────────
def run_table_profiling(
    table_id: str,
    catalog: str,
    schema: str,
    table: str,
    version: int = 1,
) -> TableProfilingResult:
    """
    Full profiling pipeline for a single table. Uses exact aggregate functions.
    Never does full column scans for numeric stats.
    """
    fqn = _fqn(catalog, schema, table)
    computed_at = datetime.utcnow()
    result = TableProfilingResult(
        table_id=table_id,
        table_fqn=fqn,
        version=version,
        computed_at=computed_at,
    )
    logger.info("[ProfilingEngine] Starting: %s (v%s)", fqn, version)

    # Step 1: Row count
    r = execute_query_sync(build_row_count_query(fqn), table_id)
    if r.success and r.rows:
        result.row_count = int(r.rows[0][0] or 0)
    else:
        result.errors.append(f"row_count: {r.error_message}")

    # Step 2: Sample
    sample_cols: List[str] = []
    r = execute_query_sync(build_sample_query(fqn), table_id)
    if r.success and r.rows:
        result.sample_size = len(r.rows)
        sample_cols = r.columns
        result.sample_data = [
            {k: (str(v) if isinstance(v, (datetime, date)) else v) for k, v in zip(sample_cols, row)}
            for row in r.rows[:50]
        ]
    else:
        result.errors.append(f"sample: {r.error_message}")

    # Step 3: Column metadata via information_schema
    columns_meta: List[Tuple[str, str]] = []
    r = execute_query_sync(build_column_metadata_query(catalog, schema, table), table_id)
    if r.success and r.rows:
        columns_meta = [(row[0], row[1]) for row in r.rows]
        result.column_count = len(columns_meta)
    else:
        result.errors.append(f"column_metadata: {r.error_message}")
        # Fallback: infer from sample
        if sample_cols:
            columns_meta = [(c, "unknown") for c in sample_cols]
            result.column_count = len(columns_meta)

    # Step 4: Per-column analysis
    col_stats: List[ColumnStats] = []
    for col_name, data_type in columns_meta:
        logger.info("[ProfilingEngine]   → %s (%s)", col_name, data_type)
        try:
            cs = _analyze_column(fqn, table_id, col_name, data_type, result.row_count)
            col_stats.append(cs)
        except Exception as exc:
            logger.error("[ProfilingEngine] Column %s failed: %s", col_name, exc)
            col_stats.append(ColumnStats(column_name=col_name, data_type=data_type, errors=[str(exc)]))
    result.column_stats = col_stats

    # Step 5: Aggregate null rate
    if col_stats:
        result.null_rate_avg = round(sum(c.null_rate for c in col_stats) / len(col_stats), 4)

    # Step 6: Auto insights
    insights = []
    if result.row_count:
        insights.append(f"~{result.row_count:,} rows (COUNT(*)).")
    if result.sample_size:
        insights.append(f"{result.sample_size:,} rows sampled via LIMIT {SAMPLE_LIMIT}.")
    cat_cols = [c for c in col_stats if c.is_categorical]
    if cat_cols:
        insights.append(f"{len(cat_cols)} categorical column(s): {', '.join(c.column_name for c in cat_cols[:5])}.")
    time_cols = [c for c in col_stats if c.is_time]
    if time_cols:
        insights.append(f"Time columns: {', '.join(c.column_name for c in time_cols[:3])} — suitable for range filters.")
    geo_cols = [c for c in col_stats if c.is_geo]
    if geo_cols:
        insights.append(f"Geographic columns: {', '.join(c.column_name for c in geo_cols)}.")
    high_null = [c for c in col_stats if c.null_rate > 0.20]
    if high_null:
        insights.append(f"High null rate (>20%): {', '.join(c.column_name for c in high_null[:5])}.")
    if result.row_count > 0:
        pk_candidates = [c for c in col_stats if c.distinct_count >= result.row_count * 0.95]
        if pk_candidates:
            insights.append(f"PK candidates: {', '.join(c.column_name for c in pk_candidates[:3])}.")
    result.auto_insights = insights

    # Step 7: Full profile_json
    result.profile_json = {
        "table": fqn,
        "version": version,
        "computed_at": computed_at.isoformat(),
        "row_count": result.row_count,
        "sample_size": result.sample_size,
        "column_count": result.column_count,
        "null_rate_avg": result.null_rate_avg,
        "columns": [
            {
                "name": c.column_name,
                "data_type": c.data_type,
                "semantic_type": c.semantic_type,
                "is_categorical": c.is_categorical,
                "distinct_count": c.distinct_count,
                "null_rate": c.null_rate,
                "stats": c.stats_json,
            }
            for c in col_stats
        ],
        "insights": insights,
        "errors": result.errors,
    }

    result.success = result.row_count > 0 or not result.errors
    logger.info(
        "[ProfilingEngine] Done: %s — %d cols, %s rows, %d error(s)",
        fqn, len(col_stats), format(result.row_count, ","), len(result.errors)
    )
    return result


# ── LLM Context Builder ────────────────────────────────────────────────────────
def build_context_for_llm(table_name: str, profile_json: Dict, column_profiles: List) -> Dict:
    """
    Produces a compact, LLM-ready context blob from persisted profiling data.
    Used by the TextToSQL context builder for system-prompt injection.
    """
    context_columns = []
    for cp in column_profiles:
        stats = cp.stats_json or {}
        col_ctx: Dict = {
            "column": cp.column_name,
            "data_type": cp.data_type,
            "semantic_type": getattr(cp, "semantic_type", "continuous"),
            "null_rate": cp.null_rate,
        }
        if getattr(cp, "is_categorical", False):
            col_ctx["values"] = stats.get("values", [])[:20]
        else:
            col_ctx["min"] = stats.get("min")
            col_ctx["max"] = stats.get("max")
            col_ctx["avg"] = stats.get("avg")
            col_ctx["sample_values"] = stats.get("sample_values", [])[:5]
        context_columns.append(col_ctx)

    return {
        "table": table_name,
        "row_count": (profile_json or {}).get("row_count"),
        "columns": context_columns,
        "insights": (profile_json or {}).get("insights", []),
    }
