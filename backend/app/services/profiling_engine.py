"""
profiling_engine.py — Real Trino-backed data profiling engine.

Runs full scan queries against Trino, computes column statistics,
detects categorical vs continuous columns, and produces structured output
ready for PostgreSQL persistence and LLM context injection.
"""

import concurrent.futures
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.config import settings
from core import execute_query_sync

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────────
CATEGORICAL_DISTINCT_THRESHOLD = 50
CATEGORICAL_COVERAGE_THRESHOLD = 0.90  # top-N values cover ≥90% → categorical

SAMPLE_LIMIT = 10_000
TOP_VALUES_LIMIT = 50
QUERY_TIMEOUT_SECONDS = (
    settings.TRINO_REQUEST_TIMEOUT
)  # Hard per-query timeout in seconds

NUMERIC_TYPES = {
    "bigint",
    "integer",
    "smallint",
    "tinyint",
    "double",
    "real",
    "decimal",
    "float",
    "number",
}
TIME_TYPES = {
    "date",
    "timestamp",
    "timestamp with time zone",
    "timestamp(3) with time zone",
    "time",
}
GEO_HINTS = {
    "lat",
    "lon",
    "latitude",
    "longitude",
    "geometry",
    "geom",
    "location",
    "coordinates",
}
TIME_HINTS = {
    "date",
    "time",
    "timestamp",
    "created_at",
    "updated_at",
    "event_at",
    "occurred_at",
    "dt",
    "day",
    "month",
    "year",
    "week",
}
# These types cannot be used with DISTINCT/GROUP BY in Trino (excludes row which we handle)
COMPLEX_TYPE_PREFIXES = ("array(", "map(", "json", "varbinary")
ROW_MAX_DEPTH = 5  # Maximum recursion depth for nested ROW types


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
    semantic_type: str = "continuous"  # categorical | continuous | time | geo
    top_values: list[dict] = field(default_factory=list)
    value_frequencies: dict[str, int] = field(default_factory=dict)
    min_value: str | None = None
    max_value: str | None = None
    avg_value: float | None = None
    median_value: float | None = None
    q25_value: float | None = None
    q75_value: float | None = None
    stddev_value: float | None = None
    sample_values: list[Any] = field(default_factory=list)
    histogram: list[dict] | None = None
    stats_json: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


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
    auto_insights: list[str] = field(default_factory=list)
    sample_data: list[dict] = field(default_factory=list)
    column_stats: list[ColumnStats] = field(default_factory=list)
    profile_json: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
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
        f"SELECT "
        f'  MIN("{col}") AS min_val, '
        f'  MAX("{col}") AS max_val, '
        f'  AVG(CAST("{col}" AS DOUBLE)) AS avg_val, '
        f'  APPROX_PERCENTILE(CAST("{col}" AS DOUBLE), ARRAY[0.25, 0.5, 0.75]) AS quants, '
        f'  STDDEV_POP(CAST("{col}" AS DOUBLE)) AS std_val '
        f'FROM {fqn} WHERE "{col}" IS NOT NULL'
    )


def build_generic_histogram_query(
    fqn: str,
    field_path: str,
    cast_expr: str,
    min_val: float,
    max_val: float,
    buckets: int = 8,
) -> str:
    if min_val == max_val:
        return f"SELECT 1 AS bucket, COUNT(*) AS cnt FROM {fqn} GROUP BY 1 ORDER BY 1"
    return f"""
WITH raw_buckets AS (
    SELECT
        CASE
            WHEN {field_path} IS NULL THEN null
            WHEN {cast_expr} >= {max_val} THEN {buckets}
            WHEN {cast_expr} <= {min_val} THEN 1
            ELSE width_bucket({cast_expr}, {min_val}, {max_val}, {buckets})
        END as bucket
    FROM {fqn}
)
SELECT
    bucket,
    COUNT(*) as cnt
FROM raw_buckets
GROUP BY bucket
ORDER BY bucket ASC NULLS LAST
"""


def build_time_stats_query(fqn: str, col: str) -> str:
    # Safely extract unix epoch stats for temporal fields
    return (
        f"SELECT "
        f'  MIN("{col}") AS min_val, '
        f'  MAX("{col}") AS max_val, '
        f'  APPROX_PERCENTILE(to_unixtime(CAST("{col}" AS TIMESTAMP)), ARRAY[0.25, 0.5, 0.75]) AS quants, '
        f'  STDDEV_POP(to_unixtime(CAST("{col}" AS TIMESTAMP))) AS std_val, '
        f'  MIN(to_unixtime(CAST("{col}" AS TIMESTAMP))) AS min_unix, '
        f'  MAX(to_unixtime(CAST("{col}" AS TIMESTAMP))) AS max_unix '
        f'FROM {fqn} WHERE "{col}" IS NOT NULL'
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
def _fqn(catalog: str, schema: str, table: str) -> str:
    return f'"{catalog}"."{schema}"."{table}"'


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert any non-JSON-serializable value to a safe primitive.

    Handles: datetime/date → ISO string, Decimal → float, bytes → hex string,
    tuples → lists, and nested dicts/lists.
    """
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _is_complex_type(dtype: str) -> bool:
    """Returns True for Trino types that cannot be used in DISTINCT/GROUP BY (not ROW)."""
    dl = dtype.lower().strip()
    return dl.startswith(COMPLEX_TYPE_PREFIXES) or dl in {"json"}


def _is_row_type(dtype: str) -> bool:
    """Returns True for Trino ROW(...) types."""
    return dtype.lower().strip().startswith("row(")


def _parse_row_fields(row_type: str) -> list[tuple[str, str]]:
    """Parse Trino row(field_name type, ...) into [(field_name, type), ...].

    Handles nested parentheses correctly.
    Example: 'row(id bigint, addr row(city varchar, zip varchar))'
    -> [('id', 'bigint'), ('addr', 'row(city varchar, zip varchar)')]
    """
    # Strip outer 'row(' and ')'
    inner = row_type.strip()
    if inner.lower().startswith("row(") and inner.endswith(")"):
        inner = inner[4:-1].strip()
    else:
        return []

    fields: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for ch in inner:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            token = current.strip()
            if token:
                # Split on first whitespace to separate name from type
                parts = token.split(None, 1)
                if len(parts) == 2:
                    fields.append((parts[0], parts[1]))
                elif len(parts) == 1:
                    fields.append((parts[0], "unknown"))
            current = ""
        else:
            current += ch
    # Last token
    token = current.strip()
    if token:
        parts = token.split(None, 1)
        if len(parts) == 2:
            fields.append((parts[0], parts[1]))
        elif len(parts) == 1:
            fields.append((parts[0], "unknown"))
    return fields


_global_trino_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=settings.PROFILER_MAX_CONCURRENT_QUERIES
)


def execute_with_timeout(query: str, table_id: str):
    """Runs execute_query_sync in a global thread pool and enforces a hard wall-clock timeout."""
    future = _global_trino_executor.submit(execute_query_sync, query, table_id)
    try:
        return future.result(timeout=QUERY_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        logger.warning(
            f"[TrinoClient] Query timed out after {QUERY_TIMEOUT_SECONDS}s: {query[:120]}"
        )
        from app.services.trino_client import TrinoExecutionResult

        return TrinoExecutionResult(
            success=False,
            error_message=f"Query timed out after {QUERY_TIMEOUT_SECONDS}s",
        )


def _detect_semantic_type(is_categorical: bool, is_geo: bool, is_time: bool) -> str:
    if is_geo:
        return "geo"
    if is_time:
        return "time"
    if is_categorical:
        return "categorical"
    return "continuous"


# ── Row-type recursive analysis ────────────────────────────────────────────────
def _analyze_row_column(
    fqn: str,
    table_id: str,
    col_path: str,
    row_type: str,
    row_count: int,
    depth: int = 0,
) -> dict:
    """Recursively profile the fields of a Trino ROW column.

    col_path: SQL expression to access the parent column, e.g. '"my_col"' or '"my_col"."addr"'
    Returns a stats_json-compatible dict with a 'children' list.
    """
    if depth >= ROW_MAX_DEPTH:
        return {
            "type": "row",
            "data_type": row_type,
            "note": "Max depth reached",
            "children": [],
        }

    fields = _parse_row_fields(row_type)
    children: list[dict] = []

    for field_name, field_type in fields:
        field_path = f'{col_path}."{field_name}"'
        field_lower = field_type.lower().strip()
        child: dict = {"name": field_name, "data_type": field_type}

        if _is_row_type(field_type):
            # Recurse into nested ROW
            child["semantic_type"] = "row"
            child["stats"] = _analyze_row_column(
                fqn, table_id, field_path, field_type, row_count, depth + 1
            )
        elif _is_complex_type(field_type):
            # Skip arrays/maps/json inside rows too
            child["semantic_type"] = "complex"
            child["stats"] = {"type": "complex", "note": "Skipped (array/map/json)"}
        else:
            # Normal field — run null ratio + top values
            child_stats: dict = {"type": field_lower}
            is_time = field_lower in TIME_TYPES or any(
                h in field_name.lower() for h in TIME_HINTS
            )
            is_geo = field_name.lower() in GEO_HINTS or "geo" in field_name.lower()
            child["is_time"] = is_time
            child["is_geo"] = is_geo

            # Null ratio via IS NULL count
            null_q = f"SELECT COUNT(*) AS total, SUM(CASE WHEN {field_path} IS NULL THEN 1 ELSE 0 END) AS nulls FROM {fqn}"
            r = execute_with_timeout(null_q, table_id)
            if r.success and r.rows:
                total = int(r.rows[0][0] or 1)
                nulls = int(r.rows[0][1] or 0)
                child_stats["null_count"] = nulls
                child_stats["null_rate"] = round(nulls / max(total, 1), 4)
                child["null_count"] = nulls
                child["null_rate"] = child_stats["null_rate"]

            # Top values (non-time, non-numeric)
            if not is_time and field_lower not in NUMERIC_TYPES:
                top_q = f"SELECT {field_path}, COUNT(*) AS cnt FROM {fqn} WHERE {field_path} IS NOT NULL GROUP BY {field_path} ORDER BY cnt DESC LIMIT {TOP_VALUES_LIMIT}"
                r = execute_with_timeout(top_q, table_id)
                if r.success and r.rows:
                    top_vals = [
                        {"value": _make_json_safe(row[0]), "count": int(row[1])}
                        for row in r.rows
                    ]
                    child_stats["top_values"] = top_vals
                    child_stats["distinct_count"] = len(top_vals)
                    child["distinct_count"] = len(top_vals)
                    child["top_values"] = top_vals

            # Full numeric stats (min/max/percentiles/stddev)
            if field_lower in NUMERIC_TYPES:
                num_q = (
                    f"SELECT MIN({field_path}), MAX({field_path}), "
                    f"AVG(CAST({field_path} AS DOUBLE)), "
                    f"APPROX_PERCENTILE(CAST({field_path} AS DOUBLE), ARRAY[0.25, 0.5, 0.75]), "
                    f"STDDEV_POP(CAST({field_path} AS DOUBLE)) "
                    f"FROM {fqn} WHERE {field_path} IS NOT NULL"
                )
                r = execute_with_timeout(num_q, table_id)
                if r.success and r.rows and r.rows[0][0] is not None:
                    row = r.rows[0]
                    child_stats["min"] = _make_json_safe(row[0])
                    child_stats["max"] = _make_json_safe(row[1])
                    child_stats["avg"] = _safe_float(row[2])
                    quants = row[3]
                    if isinstance(quants, list) and len(quants) >= 3:
                        child_stats["q25"] = quants[0]
                        child_stats["median"] = quants[1]
                        child_stats["q75"] = quants[2]
                    child_stats["stddev"] = _safe_float(row[4])
                    child["min_value"] = str(child_stats["min"])
                    child["max_value"] = str(child_stats["max"])

                    min_f = _safe_float(child_stats["min"])
                    max_f = _safe_float(child_stats["max"])
                    if min_f is not None and max_f is not None:
                        cast_expr = f"CAST({field_path} AS DOUBLE)"
                        hist_r = execute_with_timeout(
                            build_generic_histogram_query(
                                fqn, field_path, cast_expr, min_f, max_f, 8
                            ),
                            table_id,
                        )
                        if hist_r.success and hist_r.rows:
                            hist_data = []
                            step = (max_f - min_f) / 8 if max_f > min_f else 0
                            bucket_counts = {
                                int(r[0]) if r[0] is not None else "null": int(r[1])
                                for r in hist_r.rows
                            }
                            if max_f > min_f:
                                for i in range(1, 9):
                                    cnt = bucket_counts.get(i, 0)
                                    lo = min_f + (i - 1) * step
                                    hi = min_f + i * step
                                    hist_data.append(
                                        {
                                            "lo": lo,
                                            "hi": hi,
                                            "count": cnt,
                                            "label": f"{lo:g}",
                                        }
                                    )
                            else:
                                hist_data.append(
                                    {
                                        "lo": min_f,
                                        "hi": max_f,
                                        "count": bucket_counts.get(1, 0),
                                        "label": f"{min_f:g}",
                                    }
                                )
                            null_cnt = bucket_counts.get("null", 0)
                            if null_cnt > 0:
                                hist_data.append(
                                    {
                                        "lo": None,
                                        "hi": None,
                                        "count": null_cnt,
                                        "label": "Null / Unknown",
                                    }
                                )
                            child_stats["histogram"] = hist_data
                            child["histogram"] = hist_data

            # Full time stats (min/max/percentiles as unix epoch)
            elif is_time:
                time_q = (
                    f"SELECT MIN({field_path}), MAX({field_path}), "
                    f"APPROX_PERCENTILE(to_unixtime(CAST({field_path} AS TIMESTAMP)), ARRAY[0.25, 0.5, 0.75]), "
                    f"STDDEV_POP(to_unixtime(CAST({field_path} AS TIMESTAMP))), "
                    f"MIN(to_unixtime(CAST({field_path} AS TIMESTAMP))), "
                    f"MAX(to_unixtime(CAST({field_path} AS TIMESTAMP))) "
                    f"FROM {fqn} WHERE {field_path} IS NOT NULL"
                )
                r = execute_with_timeout(time_q, table_id)
                if r.success and r.rows and r.rows[0][0] is not None:
                    row = r.rows[0]
                    child_stats["min"] = _make_json_safe(row[0])
                    child_stats["max"] = _make_json_safe(row[1])
                    quants = row[2]
                    if isinstance(quants, list) and len(quants) >= 3:
                        child_stats["q25"] = quants[0]
                        child_stats["median"] = quants[1]
                        child_stats["q75"] = quants[2]
                    child_stats["stddev"] = _safe_float(row[3])
                    child["min_value"] = str(child_stats["min"])
                    child["max_value"] = str(child_stats["max"])
                    min_unix = _safe_float(row[4])
                    max_unix = _safe_float(row[5])

                    if min_unix is not None and max_unix is not None:
                        from datetime import datetime

                        cast_expr = f"to_unixtime(CAST({field_path} AS TIMESTAMP))"
                        hist_r = execute_with_timeout(
                            build_generic_histogram_query(
                                fqn, field_path, cast_expr, min_unix, max_unix, 8
                            ),
                            table_id,
                        )
                        if hist_r.success and hist_r.rows:
                            hist_data = []
                            step = (
                                (max_unix - min_unix) / 8 if max_unix > min_unix else 0
                            )
                            bucket_counts = {
                                int(r[0]) if r[0] is not None else "null": int(r[1])
                                for r in hist_r.rows
                            }
                            if max_unix > min_unix:
                                for i in range(1, 9):
                                    cnt = bucket_counts.get(i, 0)
                                    lo = min_unix + (i - 1) * step
                                    hi = min_unix + i * step
                                    hist_data.append(
                                        {
                                            "lo": datetime.fromtimestamp(
                                                lo
                                            ).isoformat(),
                                            "hi": datetime.fromtimestamp(
                                                hi
                                            ).isoformat(),
                                            "count": cnt,
                                            "label": datetime.fromtimestamp(
                                                lo
                                            ).strftime("%Y-%m-%d"),
                                        }
                                    )
                            else:
                                hist_data.append(
                                    {
                                        "lo": datetime.fromtimestamp(
                                            min_unix
                                        ).isoformat(),
                                        "hi": datetime.fromtimestamp(
                                            max_unix
                                        ).isoformat(),
                                        "count": bucket_counts.get(1, 0),
                                        "label": datetime.fromtimestamp(
                                            min_unix
                                        ).strftime("%Y-%m-%d"),
                                    }
                                )
                            null_cnt = bucket_counts.get("null", 0)
                            if null_cnt > 0:
                                hist_data.append(
                                    {
                                        "lo": None,
                                        "hi": None,
                                        "count": null_cnt,
                                        "label": "Null / Unknown",
                                    }
                                )
                            child_stats["histogram"] = hist_data
                            child["histogram"] = hist_data

            if is_time:
                child["semantic_type"] = "time"
            elif is_geo:
                child["semantic_type"] = "geo"
            elif field_lower in NUMERIC_TYPES:
                child["semantic_type"] = "continuous"
            else:
                child["semantic_type"] = "categorical"

            child["stats"] = child_stats

        children.append(child)

    return {
        "type": "row",
        "data_type": row_type,
        "children": children,
    }


# ── Per-Column Analysis ────────────────────────────────────────────────────────
def _analyze_column(
    fqn: str, table_id: str, col_name: str, data_type: str, row_count: int
) -> ColumnStats:
    stats = ColumnStats(column_name=col_name, data_type=data_type)
    dtype_lower = data_type.lower()
    col_lower = col_name.lower()

    stats.is_geo = col_lower in GEO_HINTS or "geo" in col_lower or "coord" in col_lower
    stats.is_time = dtype_lower in TIME_TYPES or any(h in col_lower for h in TIME_HINTS)

    # ROW type: recursively profile inner fields
    if _is_row_type(data_type):
        stats.semantic_type = "row"
        stats.stats_json = _analyze_row_column(
            fqn, table_id, f'"{col_name}"', data_type, row_count, depth=0
        )
        return stats

    # Skip ARRAY, MAP, JSON, varbinary — cannot GROUP BY
    if _is_complex_type(data_type):
        stats.semantic_type = "complex"
        stats.stats_json = {
            "type": "complex",
            "data_type": data_type,
            "note": "Skipped (array/map/json)",
        }
        return stats

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as inner_exec:
        f_dist = inner_exec.submit(
            execute_with_timeout, build_distinct_count_query(fqn, col_name), table_id
        )
        f_null = inner_exec.submit(
            execute_with_timeout, build_null_ratio_query(fqn, col_name), table_id
        )

        # 1. Exact distinct count
        r_dist = f_dist.result()
        if r_dist.success and r_dist.rows:
            stats.distinct_count = int(r_dist.rows[0][0] or 0)
        else:
            stats.errors.append(f"distinct_count: {r_dist.error_message}")

        # 2. Null ratio
        r_null = f_null.result()
        if r_null.success and r_null.rows:
            total = int(r_null.rows[0][0] or 1)
            non_null = int(r_null.rows[0][1] or 0)
            stats.null_count = total - non_null
            stats.null_rate = round(stats.null_count / max(total, 1), 4)
        else:
            stats.errors.append(f"null_ratio: {r_null.error_message}")

    # 3. Top values + categorical detection
    low_cardinality = 0 < stats.distinct_count < CATEGORICAL_DISTINCT_THRESHOLD
    top_values: list[dict] = []

    if low_cardinality or not stats.is_time:
        r = execute_with_timeout(build_top_values_query(fqn, col_name), table_id)
        if r.success and r.rows:
            top_values = [
                {"value": str(row[0]), "count": int(row[1])} for row in r.rows
            ]
            top_coverage = sum(v["count"] for v in top_values) / max(row_count, 1)
            stats.is_categorical = (
                low_cardinality or top_coverage >= CATEGORICAL_COVERAGE_THRESHOLD
            )
            stats.top_values = top_values
            stats.value_frequencies = {v["value"]: v["count"] for v in top_values}
        else:
            stats.errors.append(f"top_values: {r.error_message}")

    # 4. Numeric stats
    if dtype_lower in NUMERIC_TYPES and not stats.is_categorical:
        r = execute_with_timeout(build_numeric_stats_query(fqn, col_name), table_id)
        if r.success and r.rows:
            row = r.rows[0]
            stats.min_value = str(row[0]) if row[0] is not None else None
            stats.max_value = str(row[1]) if row[1] is not None else None
            stats.avg_value = _safe_float(row[2])
            quants = row[3]
            if isinstance(quants, list) and len(quants) >= 3:
                stats.q25_value = _safe_float(quants[0])
                stats.median_value = _safe_float(quants[1])
                stats.q75_value = _safe_float(quants[2])
            stats.stddev_value = _safe_float(row[4])

            # Build actual histogram
            if stats.min_value is not None and stats.max_value is not None:
                min_f = float(stats.min_value)
                max_f = float(stats.max_value)
                cast_expr = f'CAST("{col_name}" AS DOUBLE)'
                hist_r = execute_with_timeout(
                    build_generic_histogram_query(
                        fqn, f'"{col_name}"', cast_expr, min_f, max_f, 8
                    ),
                    table_id,
                )
                if hist_r.success and hist_r.rows:
                    hist_data = []
                    step = (max_f - min_f) / 8 if max_f > min_f else 0
                    bucket_counts = {
                        int(r[0]) if r[0] is not None else "null": int(r[1])
                        for r in hist_r.rows
                    }

                    if max_f > min_f:
                        for i in range(1, 9):
                            cnt = bucket_counts.get(i, 0)
                            lo = min_f + (i - 1) * step
                            hi = min_f + i * step
                            hist_data.append(
                                {"lo": lo, "hi": hi, "count": cnt, "label": f"{lo:g}"}
                            )
                    else:
                        hist_data.append(
                            {
                                "lo": min_f,
                                "hi": max_f,
                                "count": bucket_counts.get(1, 0),
                                "label": f"{min_f:g}",
                            }
                        )

                    null_cnt = bucket_counts.get("null", 0)
                    if null_cnt > 0:
                        hist_data.append(
                            {
                                "lo": None,
                                "hi": None,
                                "count": null_cnt,
                                "label": "Null / Unknown",
                            }
                        )

                    stats.histogram = hist_data
                else:
                    stats.errors.append(f"numeric_histogram: {hist_r.error_message}")
        else:
            stats.errors.append(f"numeric_stats: {r.error_message}")

    # 4b. Temporal stats
    elif stats.is_time and not stats.is_categorical:
        r = execute_with_timeout(build_time_stats_query(fqn, col_name), table_id)
        if r.success and r.rows:
            row = r.rows[0]
            stats.min_value = str(row[0]) if row[0] is not None else None
            stats.max_value = str(row[1]) if row[1] is not None else None
            quants = row[2]
            if isinstance(quants, list) and len(quants) >= 3:
                stats.q25_value = _safe_float(quants[0])
                stats.median_value = _safe_float(quants[1])
                stats.q75_value = _safe_float(quants[2])
            stats.stddev_value = _safe_float(row[3])
            min_unix = _safe_float(row[4])
            max_unix = _safe_float(row[5])

            # Build actual histogram for time
            if min_unix is not None and max_unix is not None:
                from datetime import datetime

                cast_expr = f'to_unixtime(CAST("{col_name}" AS TIMESTAMP))'
                hist_r = execute_with_timeout(
                    build_generic_histogram_query(
                        fqn, f'"{col_name}"', cast_expr, min_unix, max_unix, 8
                    ),
                    table_id,
                )
                if hist_r.success and hist_r.rows:
                    hist_data = []
                    step = (max_unix - min_unix) / 8 if max_unix > min_unix else 0
                    bucket_counts = {
                        int(r[0]) if r[0] is not None else "null": int(r[1])
                        for r in hist_r.rows
                    }

                    if max_unix > min_unix:
                        for i in range(1, 9):
                            cnt = bucket_counts.get(i, 0)
                            lo = min_unix + (i - 1) * step
                            hi = min_unix + i * step
                            hist_data.append(
                                {
                                    "lo": datetime.fromtimestamp(lo).isoformat(),
                                    "hi": datetime.fromtimestamp(hi).isoformat(),
                                    "count": cnt,
                                    "label": datetime.fromtimestamp(lo).strftime(
                                        "%Y-%m-%d"
                                    ),
                                }
                            )
                    else:
                        hist_data.append(
                            {
                                "lo": datetime.fromtimestamp(min_unix).isoformat(),
                                "hi": datetime.fromtimestamp(max_unix).isoformat(),
                                "count": bucket_counts.get(1, 0),
                                "label": datetime.fromtimestamp(min_unix).strftime(
                                    "%Y-%m-%d"
                                ),
                            }
                        )

                    null_cnt = bucket_counts.get("null", 0)
                    if null_cnt > 0:
                        hist_data.append(
                            {
                                "lo": None,
                                "hi": None,
                                "count": null_cnt,
                                "label": "Null / Unknown",
                            }
                        )

                    stats.histogram = hist_data
                else:
                    stats.errors.append(f"time_histogram: {hist_r.error_message}")
        else:
            stats.errors.append(f"time_stats: {r.error_message}")

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
            "q25": stats.q25_value,
            "q75": stats.q75_value,
            "stddev": stats.stddev_value,
            "distinct_count": stats.distinct_count,
            "null_rate": stats.null_rate,
            "sample_values": [v["value"] for v in top_values[:10]],
        }
        if stats.histogram is not None:
            stats.stats_json["histogram"] = stats.histogram

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
    sample_cols: list[str] = []
    r = execute_query_sync(build_sample_query(fqn), table_id)
    if r.success and r.rows:
        result.sample_size = len(r.rows)
        sample_cols = r.columns
        result.sample_data = [
            _make_json_safe(dict(zip(sample_cols, row, strict=False)))
            for row in r.rows[:50]
        ]
    else:
        result.errors.append(f"sample: {r.error_message}")

    # Step 3: Column metadata via information_schema
    columns_meta: list[tuple[str, str]] = []
    r = execute_query_sync(
        build_column_metadata_query(catalog, schema, table), table_id
    )
    if r.success and r.rows:
        columns_meta = [(row[0], row[1]) for row in r.rows]
        result.column_count = len(columns_meta)
    else:
        result.errors.append(f"column_metadata: {r.error_message}")
        # Fallback: infer from sample
        if sample_cols:
            columns_meta = [(c, "unknown") for c in sample_cols]
            result.column_count = len(columns_meta)

    # Step 4: Per-column analysis (Parallel execution across columns)
    col_stats: list[ColumnStats] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(30, len(columns_meta) or 1)
    ) as worker_executor:
        futures = []
        for col_name, data_type in columns_meta:
            futures.append(
                worker_executor.submit(
                    _analyze_column,
                    fqn,
                    table_id,
                    col_name,
                    data_type,
                    result.row_count,
                )
            )

        for future, (col_name, data_type) in zip(futures, columns_meta, strict=False):
            logger.info("[ProfilingEngine]   → %s (%s)", col_name, data_type)
            try:
                cs = future.result()
                col_stats.append(cs)
            except Exception as exc:
                logger.error("[ProfilingEngine] Column %s failed: %s", col_name, exc)
                col_stats.append(
                    ColumnStats(
                        column_name=col_name, data_type=data_type, errors=[str(exc)]
                    )
                )

    result.column_stats = col_stats

    # Step 5: Aggregate null rate
    if col_stats:
        result.null_rate_avg = round(
            sum(c.null_rate for c in col_stats) / len(col_stats), 4
        )

    # Step 6: Auto insights
    insights = []
    if result.row_count:
        insights.append(f"~{result.row_count:,} rows (COUNT(*)).")
    if result.sample_size:
        insights.append(
            f"{result.sample_size:,} rows sampled via LIMIT {SAMPLE_LIMIT}."
        )
    cat_cols = [c for c in col_stats if c.is_categorical]
    if cat_cols:
        insights.append(
            f"{len(cat_cols)} categorical column(s): {', '.join(c.column_name for c in cat_cols[:5])}."
        )
    time_cols = [c for c in col_stats if c.is_time]
    if time_cols:
        insights.append(
            f"Time columns: {', '.join(c.column_name for c in time_cols[:3])} — suitable for range filters."
        )
    geo_cols = [c for c in col_stats if c.is_geo]
    if geo_cols:
        insights.append(
            f"Geographic columns: {', '.join(c.column_name for c in geo_cols)}."
        )
    high_null = [c for c in col_stats if c.null_rate > 0.20]
    if high_null:
        insights.append(
            f"High null rate (>20%): {', '.join(c.column_name for c in high_null[:5])}."
        )
    if result.row_count > 0:
        pk_candidates = [
            c for c in col_stats if c.distinct_count >= result.row_count * 0.95
        ]
        if pk_candidates:
            insights.append(
                f"PK candidates: {', '.join(c.column_name for c in pk_candidates[:3])}."
            )
    result.auto_insights = insights

    # Step 7: Full profile_json — sanitize all values before DB commit
    result.profile_json = _make_json_safe(
        {
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
    )
    # Also sanitize sample_data and per-column stats_json
    result.sample_data = _make_json_safe(result.sample_data)
    for c in col_stats:
        c.stats_json = _make_json_safe(c.stats_json)

    result.success = result.row_count > 0 or not result.errors
    logger.info(
        "[ProfilingEngine] Done: %s — %d cols, %s rows, %d error(s)",
        fqn,
        len(col_stats),
        format(result.row_count, ","),
        len(result.errors),
    )
    return result


# ── LLM Context Builder ────────────────────────────────────────────────────────
def build_context_for_llm(
    table_name: str, profile_json: dict, column_profiles: list
) -> dict:
    """
    Produces a compact, LLM-ready context blob from persisted profiling data.
    Used by the TextToSQL context builder for system-prompt injection.
    """
    context_columns = []
    for cp in column_profiles:
        stats = cp.stats_json or {}
        col_ctx: dict = {
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


# ── One-time LLM Table Summarization (called during profiling) ─────────────────

def generate_table_summary(result: "TableProfilingResult") -> str:
    """
    Generate a ≤3-sentence plain-English description of a table using the LLM.
    Called once at the end of `_run_profile_job` and stored in EnrichmentVersion.

    Uses the same LLM endpoint as the agent (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL).
    Returns an empty string on any failure so profiling is never blocked.
    """
    import os

    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key = os.environ.get("LLM_API_KEY", "ollama")
    llm_model = os.environ.get("LLM_MODEL", "gemma4:e4b")

    try:
        col_lines = []
        for c in result.column_stats[:30]:
            parts = [f"{c.column_name} ({c.data_type}, {c.semantic_type})"]
            if c.is_categorical and c.top_values:
                vals = [str(v["value"]) for v in c.top_values[:5]]
                parts.append(f"values: {', '.join(vals)}")
            elif c.min_value or c.max_value:
                parts.append(f"range: {c.min_value}–{c.max_value}")
            col_lines.append(" — ".join(parts))

        prompt = (
            f"Table: {result.table_fqn}\n"
            f"Row count: {result.row_count:,}\n"
            f"Columns ({result.column_count} total):\n"
            + "\n".join(f"  • {l}" for l in col_lines)
            + "\n\nWrite a concise ≤3-sentence description of this table's purpose, "
            "what business domain it represents, and which columns are most important "
            "for querying. Be specific about what the table contains."
        )

        import httpx, json as _json

        payload = {
            "model": llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "stream": False,
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{llm_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("[ProfilingEngine] generate_table_summary failed: %s", exc)
        return ""
