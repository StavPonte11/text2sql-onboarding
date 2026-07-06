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
from typing import Any, Optional, TypedDict

class PrecomputedMetrics(TypedDict, total=False):
    null_count: int
    null_rate: float
    distinct_count: int
    min: Any
    max: Any
    avg: Any
    quants: list[Any]
    stddev: Any
    min_unix: Any
    max_unix: Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field
import os

from core import TrinoExecutionResult

from core.trino import execute_query_sync

import os

logger = logging.getLogger(__name__)

from core.config import settings

# ── Thresholds ─────────────────────────────────────────────────────────────────
SAMPLE_PERCENT = 10  # TABLESAMPLE BERNOULLI(10)
SAMPLE_LIMIT = 10_000
TOP_VALUES_LIMIT = 50
MIN_EXAMPLES = 3

CATEGORICAL_DISTINCT_THRESHOLD = 50   # Max unique values for standard string categories
NUMERIC_CATEGORICAL_THRESHOLD = 15    # Strict limit for integers (e.g., status codes)

# 4-Layer String Engine Thresholds
CARDINALITY_RATIO_IDENTIFIER = 0.90   # Layer 1 Uniqueness
CARDINALITY_RATIO_DISPERSION = 0.50   # Layer 3 Dispersion trap
TOP_10_COVERAGE_DISPERSION = 0.10     # Layer 3 Coverage trap

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

STRING_TYPES = {
    "varchar",
    "char",
    "text",
    "string",
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
class HistogramBucket(BaseModel):
    lo: Any
    hi: Any
    count: int
    label: str

class BaseStats(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=False)

class CategoricalStats(BaseStats):
    values: list[Any] = Field(default_factory=list)
    frequencies: dict[str, int] = Field(default_factory=dict)
    top_values_raw: list[dict] = Field(default_factory=list)

class ContinuousStats(BaseStats):
    min: float | int | str | None = None
    max: float | int | str | None = None
    avg: float | None = None
    stddev: float | None = None
    q25: float | None = None
    median: float | None = None
    q75: float | None = None
    histogram: list[HistogramBucket] | None = None
    examples: list[str] | None = None

class TimeStats(BaseStats):
    min: float | int | str | None = None
    max: float | int | str | None = None
    stddev: float | None = None
    q25: float | None = None
    median: float | None = None
    q75: float | None = None
    histogram: list[HistogramBucket] | None = None
    examples: list[str] | None = None


@dataclass
class ColumnStats:
    column_name: str
    data_type: str
    null_count: int = 0
    null_rate: float = 0.0
    distinct_count: int = 0

    is_categorical: bool = False
    is_large_categorical: bool = False
    is_free_text: bool = False
    is_geo: bool = False
    is_time: bool = False
    is_boolean: bool = False
    is_continuous: bool = False
    semantic_type: str = "continuous"  # categorical | continuous | temporal | geo | large_categorical | free_text | boolean

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
    return f'SELECT COUNT(DISTINCT {col}) FROM {fqn}'

# def build_distinct_count_query(fqn: str, col: str) -> str:
#     # Swapped to APPROX_DISTINCT to prevent Trino worker OOMs and timeouts
#     return f'SELECT APPROX_DISTINCT({col}) FROM {fqn}'

def build_null_ratio_query(fqn: str, col: str) -> str:
    return f'SELECT COUNT(*) AS total, COUNT({col}) AS non_null FROM {fqn}'


def build_top_values_query(fqn: str, col: str, limit: int = TOP_VALUES_LIMIT) -> str:
    return (
        f'SELECT {col}, COUNT(*) AS cnt '
        f'FROM {fqn} GROUP BY {col} ORDER BY cnt DESC LIMIT {limit}'
    )


def build_numeric_stats_query(fqn: str, col: str) -> str:
    return (
        f"SELECT "
        f'  MIN({col}) AS min_val, '
        f'  MAX({col}) AS max_val, '
        f'  AVG(TRY_CAST({col} AS DOUBLE)) AS avg_val, '
        f'  APPROX_PERCENTILE(TRY_CAST({col} AS DOUBLE), ARRAY[0.25, 0.5, 0.75]) AS quants, '
        f'  STDDEV_POP(TRY_CAST({col} AS DOUBLE)) AS std_val '
        f'FROM {fqn} WHERE {col} IS NOT NULL'
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


def build_time_stats_query(fqn: str, col: str, data_type: str = "") -> str:
    dtype = data_type.lower()
    if any(t in dtype for t in ["int", "double", "float", "real", "decimal", "numeric"]):
        return (
            f"SELECT "
            f'  MIN({col}) AS min_val, '
            f'  MAX({col}) AS max_val, '
            f'  APPROX_PERCENTILE(TRY_CAST({col} AS DOUBLE), ARRAY[0.25, 0.5, 0.75]) AS quants, '
            f'  STDDEV_POP(TRY_CAST({col} AS DOUBLE)) AS std_val, '
            f'  MIN(TRY_CAST({col} AS DOUBLE)) AS min_unix, '
            f'  MAX(TRY_CAST({col} AS DOUBLE)) AS max_unix '
            f'FROM {fqn} WHERE {col} IS NOT NULL'
        )
    return (
        f"SELECT "
        f'  MIN({col}) AS min_val, '
        f'  MAX({col}) AS max_val, '
        f'  APPROX_PERCENTILE(to_unixtime(TRY_CAST({col} AS TIMESTAMP)), ARRAY[0.25, 0.5, 0.75]) AS quants, '
        f'  STDDEV_POP(to_unixtime(TRY_CAST({col} AS TIMESTAMP))) AS std_val, '
        f'  MIN(to_unixtime(TRY_CAST({col} AS TIMESTAMP))) AS min_unix, '
        f'  MAX(to_unixtime(TRY_CAST({col} AS TIMESTAMP))) AS max_unix '
        f'FROM {fqn} WHERE {col} IS NOT NULL'
    )

def build_combined_profiling_query(fqn: str, columns_meta: list[tuple]) -> str | None:
    select_parts = ["COUNT(*) AS __row_count"]
    has_any = False

    for col in columns_meta:
        col_name, data_type = col[0], col[1]
        if _is_row_type(data_type) or _is_complex_type(data_type):
            continue

        safe_col = f'"{col_name}"'
        select_parts.append(f'COUNT({safe_col}) AS "{col_name}__non_null"')
        select_parts.append(f'APPROX_DISTINCT({safe_col}) AS "{col_name}__distinct"')
        has_any = True

        dtype_lower = data_type.lower()
        if any(t in dtype_lower for t in NUMERIC_TYPES):
            select_parts.append(f'MIN({safe_col}) AS "{col_name}__min"')
            select_parts.append(f'MAX({safe_col}) AS "{col_name}__max"')
            select_parts.append(f'AVG(TRY_CAST({safe_col} AS DOUBLE)) AS "{col_name}__avg"')
            select_parts.append(f'APPROX_PERCENTILE(TRY_CAST({safe_col} AS DOUBLE), ARRAY[0.25, 0.5, 0.75]) AS "{col_name}__quants"')
            select_parts.append(f'STDDEV_POP(TRY_CAST({safe_col} AS DOUBLE)) AS "{col_name}__std"')
        elif any(t in dtype_lower for t in TIME_TYPES):
            select_parts.append(f'MIN({safe_col}) AS "{col_name}__min"')
            select_parts.append(f'MAX({safe_col}) AS "{col_name}__max"')
            select_parts.append(f'APPROX_PERCENTILE(to_unixtime(TRY_CAST({safe_col} AS TIMESTAMP)), ARRAY[0.25, 0.5, 0.75]) AS "{col_name}__quants"')
            select_parts.append(f'STDDEV_POP(to_unixtime(TRY_CAST({safe_col} AS TIMESTAMP))) AS "{col_name}__std"')
            select_parts.append(f'MIN(to_unixtime(TRY_CAST({safe_col} AS TIMESTAMP))) AS "{col_name}__min_unix"')
            select_parts.append(f'MAX(to_unixtime(TRY_CAST({safe_col} AS TIMESTAMP))) AS "{col_name}__max_unix"')

    if not has_any:
        return None

    return f"SELECT {', '.join(select_parts)} FROM {fqn}"


def parse_combined_result(columns_meta: list[tuple], row_dict: dict[str, Any], row_count: int) -> dict[str, PrecomputedMetrics]:
    col_metrics = {}
    for col in columns_meta:
        col_name, data_type = col[0], col[1]
        if _is_row_type(data_type) or _is_complex_type(data_type):
            continue

        col_key = col_name.lower()
        non_null_key = f"{col_key}__non_null"
        distinct_key = f"{col_key}__distinct"
        if non_null_key not in row_dict:
            continue

        non_nulls = int(row_dict[non_null_key] or 0)
        nulls = row_count - non_nulls
        distinct_count = int(row_dict[distinct_key] or 0)

        metrics: PrecomputedMetrics = {
            "null_count": nulls,
            "null_rate": round(nulls / max(row_count, 1), 4),
            "distinct_count": distinct_count,
        }

        dtype_lower = data_type.lower()
        if any(t in dtype_lower for t in NUMERIC_TYPES):
            metrics["min"] = row_dict.get(f"{col_key}__min")
            metrics["max"] = row_dict.get(f"{col_key}__max")
            metrics["avg"] = row_dict.get(f"{col_key}__avg")
            metrics["quants"] = row_dict.get(f"{col_key}__quants") # type: ignore
            metrics["stddev"] = row_dict.get(f"{col_key}__std")
        elif any(t in dtype_lower for t in TIME_TYPES):
            metrics["min"] = row_dict.get(f"{col_key}__min")
            metrics["max"] = row_dict.get(f"{col_key}__max")
            metrics["quants"] = row_dict.get(f"{col_key}__quants") # type: ignore
            metrics["stddev"] = row_dict.get(f"{col_key}__std")
            metrics["min_unix"] = row_dict.get(f"{col_key}__min_unix")
            metrics["max_unix"] = row_dict.get(f"{col_key}__max_unix")

        col_metrics[col_name] = metrics
    return col_metrics

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





def _is_complex_type(dtype: str) -> bool:
    """Returns True for Trino types that cannot be used in DISTINCT/GROUP BY (not ROW)."""
    dl = dtype.lower().strip()
    return dl.startswith(COMPLEX_TYPE_PREFIXES) or dl in {"json"}


def _is_row_type(dtype: str) -> bool:
    """Returns True for Trino ROW(...) types."""
    return dtype.lower().strip().startswith("row(")


_global_trino_executor = None

def _get_executor():
    global _global_trino_executor
    if _global_trino_executor is None:
        _global_trino_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=settings.PROFILER_MAX_CONCURRENT_QUERIES
        )
    return _global_trino_executor


def execute_with_timeout(query: str, table_id: str):
    """Runs execute_query_sync in a global thread pool and enforces a hard wall-clock timeout."""
    request_timeout = settings.TRINO_REQUEST_TIMEOUT
    future = _get_executor().submit(execute_query_sync, query, table_id)
    try:
        return future.result(timeout=request_timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            f"[TrinoClient] Query timed out after {request_timeout}s: {query[:120]}"
        )
        return TrinoExecutionResult(
            success=False,
            columns=[],
            rows=[],
            error_message=f"Query timed out after {request_timeout}s"
        )


def _fetch_one(query: str, table_id: str, default: Any = None) -> Any:
    """Helper to fetch a single row and handle boilerplates."""
    res = execute_with_timeout(query, table_id)
    if not res.success:
        raise Exception(f"Query failed: {res.error_message}")
    if res.rows:
        return res.rows[0]
    return default

def _fetch_all(query: str, table_id: str) -> list[tuple]:
    """Helper to fetch all rows safely."""
    res = execute_with_timeout(query, table_id)
    if not res.success:
        raise Exception(f"Query failed: {res.error_message}")
    return res.rows if res.rows else []


def _classify_semantic_type(
    col_name: str,
    data_type: str,
    row_count: int,
    null_count: int,
    distinct_count: int,
    t10_coverage: float | None = None  # Optional parameter for Stage 2
) -> str:
    """Classifies column data dynamically based on limits and statistical ratios."""
    dtype_lower = data_type.lower()
    col_lower = col_name.lower()

    # 1. Base Type Flags
    is_geo = col_lower in GEO_HINTS or "geo" in col_lower or "coord" in col_lower
    is_time = dtype_lower in TIME_TYPES or any(h in col_lower for h in TIME_HINTS)
    is_string = any(t in dtype_lower for t in STRING_TYPES)
    is_numeric = any(t in dtype_lower for t in NUMERIC_TYPES)

    # 2. Math Setup
    N = max(row_count - null_count, 1) # Non-null rows
    U = distinct_count                 # Unique values
    R = U / N if N > 0 else 0          # Uniqueness Ratio

    # ==========================================
    # LAYER 1: Explicit Database Types & Fast Pass
    # ==========================================
    if dtype_lower == "boolean":
        return "boolean"
    if is_geo:
        return "geo"
    if is_time:
        return "time"

    # ==========================================
    # LAYER 2: Constants & The Boolean Heuristic
    # ==========================================
    if U == 1:
        return "categorical" # It's a constant value. Let the LLM see it exactly.
    if U == 2:
        return "boolean"     # 2 states (e.g., 1/0, Y/N). Nulls are ignored by U.

    # ==========================================
    # LAYER 3: The Numeric Split
    # ==========================================
    if is_numeric:
        if U <= NUMERIC_CATEGORICAL_THRESHOLD:
            return "categorical"
        return "continuous"

    # ==========================================
    # LAYER 4: High Uniqueness (Identifiers)
    # ==========================================
    if R >= CARDINALITY_RATIO_IDENTIFIER:
        return "free_text"

    # ==========================================
    # LAYER 5: The Anchor Category (Strict Lists)
    # ==========================================
    if U <= CATEGORICAL_DISTINCT_THRESHOLD:
        return "categorical"

    # ==========================================
    # LAYER 6: The String "Gray Zone" (T10 Trap)
    # ==========================================
    if is_string:
        # Stage 1: We don't have the T10 math yet. Tell the engine to go fetch it.
        if t10_coverage is None:
            return "requires_t10_check"

        # Stage 2: We have the T10 math. Apply the dispersion trap!
        # If the data is moderately dispersed (R >= 0.50) AND the top 10 values
        # barely cover any ground (<= 10%), it is noisy free text.
        if R >= CARDINALITY_RATIO_DISPERSION and t10_coverage <= TOP_10_COVERAGE_DISPERSION:
            return "free_text"

        # Otherwise, it has enough repetition to be a searchable category (like Company Name).
        return "large_categorical"

    # Default catch-all for any string that slipped through
    return "continuous"



# ── Stats Extraction Helpers ───────────────────────────────────────────────────
def _build_histogram_data(fqn: str, field_path: str, table_id: str, min_val: float, max_val: float, is_time: bool = False, data_type: str = "") -> list[dict]:
    dtype = data_type.lower()
    if is_time and not any(t in dtype for t in ["int", "double", "float", "real", "decimal", "numeric"]):
        cast_expr = f'to_unixtime(TRY_CAST({field_path} AS TIMESTAMP))'
    else:
        cast_expr = f'TRY_CAST({field_path} AS DOUBLE)'
    rows = _fetch_all(build_generic_histogram_query(fqn, field_path, cast_expr, min_val, max_val, 8), table_id)
    if not rows: return []

    hist_data = []
    step = (max_val - min_val) / 8 if max_val > min_val else 0
    bucket_counts = {int(r[0]) if r[0] is not None else "null": int(r[1]) for r in rows}

    for i in range(1, 9):
        cnt = bucket_counts.get(i, 0)
        lo, hi = min_val + (i - 1) * step, min_val + i * step
        if is_time:
            hist_data.append({
                "lo": datetime.fromtimestamp(lo).isoformat() if max_val > min_val else datetime.fromtimestamp(min_val).isoformat(),
                "hi": datetime.fromtimestamp(hi).isoformat() if max_val > min_val else datetime.fromtimestamp(max_val).isoformat(),
                "count": cnt if max_val > min_val else bucket_counts.get(1, 0),
                "label": datetime.fromtimestamp(lo).strftime("%Y-%m-%d"),
            })
        else:
            hist_data.append({
                "lo": lo if max_val > min_val else min_val,
                "hi": hi if max_val > min_val else max_val,
                "count": cnt if max_val > min_val else bucket_counts.get(1, 0),
                "label": f"{lo:g}",
            })
        if max_val <= min_val: break # Constant value, single bucket needed

    if "null" in bucket_counts:
        hist_data.append({"lo": None, "hi": None, "count": bucket_counts["null"], "label": "Null / Unknown"})
    return hist_data

def _extract_continuous_stats(fqn: str, field_path: str, table_id: str, precomputed: PrecomputedMetrics | None = None) -> ContinuousStats | None:
    stats = None
    if precomputed:
        quants = precomputed.get("quants")
        stats = ContinuousStats(
            min=precomputed.get("min"),
            max=precomputed.get("max"),
            avg=precomputed.get("avg"),
            stddev=precomputed.get("stddev"),
            q25=quants[0] if isinstance(quants, list) and len(quants) >= 3 else None,
            median=quants[1] if isinstance(quants, list) and len(quants) >= 3 else None,
            q75=quants[2] if isinstance(quants, list) and len(quants) >= 3 else None,
        )
            
    if not stats or stats.min is None or stats.max is None:
        row = _fetch_one(build_numeric_stats_query(fqn, field_path), table_id)
        if not row or row[0] is None: return None
        quants = row[3]
        stats = ContinuousStats(
            min=row[0],
            max=row[1],
            avg=row[2],
            stddev=row[4],
            q25=quants[0] if isinstance(quants, list) and len(quants) >= 3 else None,
            median=quants[1] if isinstance(quants, list) and len(quants) >= 3 else None,
            q75=quants[2] if isinstance(quants, list) and len(quants) >= 3 else None,
        )

    if stats.min is not None and stats.max is not None:
        stats.histogram = _build_histogram_data(fqn, field_path, table_id, float(stats.min), float(stats.max))
        
    return stats

def _extract_time_stats(fqn: str, field_path: str, data_type: str, table_id: str, precomputed: PrecomputedMetrics | None = None, sample_data: list[dict] | None = None) -> TimeStats | None:
    stats = None
    min_unix = max_unix = None
    
    if precomputed:
        quants = precomputed.get("quants")
        stats = TimeStats(
            min=precomputed.get("min"),
            max=precomputed.get("max"),
            stddev=precomputed.get("stddev"),
            q25=quants[0] if isinstance(quants, list) and len(quants) >= 3 else None,
            median=quants[1] if isinstance(quants, list) and len(quants) >= 3 else None,
            q75=quants[2] if isinstance(quants, list) and len(quants) >= 3 else None,
        )
        min_unix = precomputed.get("min_unix")
        max_unix = precomputed.get("max_unix")
        if stats.min is None:
            stats = None
            
    if not stats:
        row = _fetch_one(build_time_stats_query(fqn, field_path, data_type), table_id)
        if not row or row[0] is None: return None
        quants = row[2]
        stats = TimeStats(
            min=row[0],
            max=row[1],
            stddev=row[3],
            q25=quants[0] if isinstance(quants, list) and len(quants) >= 3 else None,
            median=quants[1] if isinstance(quants, list) and len(quants) >= 3 else None,
            q75=quants[2] if isinstance(quants, list) and len(quants) >= 3 else None,
        )
        min_unix, max_unix = row[4], row[5]

    dtype = data_type.lower()

    try:
        min_unix = float(min_unix) if min_unix is not None else None
        max_unix = float(max_unix) if max_unix is not None else None
    except (TypeError, ValueError):
        min_unix = max_unix = None

    if min_unix is not None and max_unix is not None:
        stats.histogram = _build_histogram_data(fqn, field_path, table_id, min_unix, max_unix, is_time=True, data_type=data_type)

    # Extract examples from sample_data to avoid extra query
    examples = []
    col_name = field_path.strip('"')
    if sample_data:
        for r in sample_data:
            val = r.get(col_name)
            if val is not None and val != "":
                val_str = str(val)
                if val_str not in examples:
                    examples.append(val_str)
                if len(examples) >= 3:
                    break
    if not examples:
        ex_rows = _fetch_all(f"SELECT DISTINCT {field_path} FROM {fqn} WHERE {field_path} IS NOT NULL LIMIT 3", table_id)
        examples = [str(r[0]) for r in ex_rows]
        
    stats.examples = examples
    return stats

def _extract_categorical_stats(fqn: str, field_path: str, table_id: str, limit: int = TOP_VALUES_LIMIT) -> CategoricalStats | None:
    rows = _fetch_all(build_top_values_query(fqn, field_path, limit=limit), table_id)
    if not rows:
        return None
    top_vals = [{"value": _make_json_safe(r[0]), "count": int(r[1])} for r in rows]
    stats = CategoricalStats(
        values=[v["value"] for v in top_vals],
        frequencies={str(v["value"]): v["count"] for v in top_vals},
        top_values_raw=top_vals
    )
    return stats

# ── Extraction Pipelines ───────────────────────────────────────────────────────
def _process_column_metrics(fqn: str, table_id: str, field_path: str, field_name: str, field_type: str, row_count: int, precomputed: PrecomputedMetrics | None = None, sample_data: list[dict] | None = None) -> dict:
    """Shared pipeline for semantic detection and stat extraction for both root columns and row children."""
    stats: dict[str, Any] = {"type": field_type.lower()}

    if precomputed is None:
        null_row = _fetch_one(build_null_ratio_query(fqn, field_path), table_id, default=(1, 0))
        if null_row is None:
            logger.warning(f"Null count query failed for {field_path}. Defaulting to 0.")
            null_row = (1, 0)
        total, non_nulls = int(null_row[0] or 1), int(null_row[1] or 0)
        nulls = total - non_nulls

        dist_row = _fetch_one(build_distinct_count_query(fqn, field_path), table_id, default=(0,))
        if dist_row is None:
            logger.warning(f"Distinct count query failed for {field_path}. Defaulting to 0.")
            distinct_count = 0
        else:
            distinct_count = int(dist_row[0] or 0)
    else:
        nulls = precomputed["null_count"]
        distinct_count = precomputed["distinct_count"]
        total = row_count

    semantic_type = _classify_semantic_type(field_name, field_type, row_count, nulls, distinct_count)
    t10_rows = None
    if semantic_type == "requires_t10_check":
        t10_rows = _fetch_all(build_top_values_query(fqn, field_path, limit=10), table_id)
        t10_coverage = sum(int(r[1]) for r in t10_rows) / max(row_count - nulls, 1) if t10_rows else 0.0
        semantic_type = _classify_semantic_type(field_name, field_type, row_count, nulls, distinct_count, t10_coverage=t10_coverage)

    stats.update({
        "null_count": nulls,
        "null_rate": round(nulls / max(total, 1), 4),
        "distinct_count": distinct_count,
        "semantic_type": semantic_type
        })

    if semantic_type in ("categorical", "boolean"):
        cat_stats = _extract_categorical_stats(fqn, field_path, table_id)
        if cat_stats:
            cat_dict = cat_stats.model_dump(exclude_none=True)
            stats.update(cat_dict)
            if semantic_type == "boolean":
                stats["examples"] = cat_dict.get("values", [])[:3]

    elif semantic_type == "large_categorical":
        if t10_rows is not None:
            stats["examples"] = [str(r[0]) for r in t10_rows[:3]]
        else:
            ex_rows = _fetch_all(build_top_values_query(fqn, field_path, limit=3), table_id)
            stats["examples"] = [str(r[0]) for r in ex_rows]

    elif semantic_type == "continuous":
        c_stats = _extract_continuous_stats(fqn, field_path, table_id, precomputed)
        if c_stats:
            stats.update(c_stats.model_dump(exclude_none=True))

    elif semantic_type == "time":
        t_stats = _extract_time_stats(fqn, field_path, field_type, table_id, precomputed, sample_data)
        if t_stats:
            stats.update(t_stats.model_dump(exclude_none=True))

    return stats


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


    # Simple inline parser for brevity (matches your original)
    def parse_fields(rt):
        inner = rt.strip()[4:-1].strip() if rt.lower().startswith("row(") else rt
        return [tuple(p.strip().split(None, 1)) if len(p.strip().split(None, 1)) == 2 else (p.strip(), "unknown") for p in inner.split(',')]

    fields = parse_fields(row_type)
    children: list[dict] = []

    for field_name, field_type in fields:
        field_path = f'{col_path}."{field_name}"'
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
            extracted = _process_column_metrics(fqn, table_id, field_path, field_name, field_type, row_count)
            child["semantic_type"] = extracted["semantic_type"]
            child["null_rate"] = extracted["null_rate"]
            child["distinct_count"] = extracted["distinct_count"]
            child["stats"] = extracted

        children.append(child)

    return {
        "type": "row",
        "data_type": row_type,
        "children": children,
    }


# ── Per-Column Analysis ────────────────────────────────────────────────────────
def _analyze_column(
    fqn: str, table_id: str, col_name: str, data_type: str, row_count: int,
    precomputed: PrecomputedMetrics | None = None,
    sample_data: list[dict] | None = None,
) -> ColumnStats:
    stats = ColumnStats(column_name=col_name, data_type=data_type)
    safe_col = f'"{col_name}"'

    if _is_row_type(data_type):
        stats.semantic_type = "row"
        stats.stats_json = _analyze_row_column(
            fqn, table_id, safe_col, data_type, row_count, depth=0
        )
        return stats

    if _is_complex_type(data_type):
        stats.semantic_type = "complex"
        stats.stats_json = {
            "type": "complex",
            "data_type": data_type,
            "note": "Skipped (array/map/json)",
        }
        return stats

    metrics = _process_column_metrics(fqn, table_id, safe_col, col_name, data_type, row_count, precomputed, sample_data)

    # Map back to ColumnStats dataclass
    stats.semantic_type = metrics.pop("semantic_type")
    setattr(stats, f"is_{stats.semantic_type}", True)

    stats.null_count = metrics.pop("null_count", 0)
    stats.null_rate = metrics.pop("null_rate", 0.0)
    stats.distinct_count = metrics.pop("distinct_count", 0)

    # Populate specific fields based on semantic type
    if "top_values_raw" in metrics:
        stats.top_values = metrics.pop("top_values_raw")
        stats.value_frequencies = metrics.pop("frequencies", {})
    if "examples" in metrics: stats.sample_values = metrics["examples"]
    if "min" in metrics: stats.min_value = str(metrics["min"])
    if "max" in metrics: stats.max_value = str(metrics["max"])
    if "avg" in metrics: stats.avg_value = metrics["avg"]
    if "median" in metrics: stats.median_value = metrics["median"]
    if "q25" in metrics: stats.q25_value = metrics["q25"]
    if "q75" in metrics: stats.q75_value = metrics["q75"]
    if "stddev" in metrics: stats.stddev_value = metrics["stddev"]
    if "histogram" in metrics: stats.histogram = metrics["histogram"]

    # Re-assemble stats_json for persistence
    stats.stats_json = {"type": stats.semantic_type, "distinct_count": stats.distinct_count, "null_rate": stats.null_rate}
    stats.stats_json.update(metrics) # Merges in any remaining keys (values, examples, min, max, etc)

    return stats


# ── Main Profiler ──────────────────────────────────────────────────────────────
def run_table_profiling(table_id: str, catalog: str, schema: str, table: str, version: int = 1) -> TableProfilingResult:
    fqn = _fqn(catalog, schema, table)
    computed_at = datetime.now()
    result = TableProfilingResult(table_id=table_id, table_fqn=fqn, version=version, computed_at=computed_at)
    logger.info("[ProfilingEngine] Starting: %s (v%s)", fqn, version)

    row_count_res = _fetch_one(build_row_count_query(fqn), table_id, default=(0,))
    result.row_count = int(row_count_res[0] or 0)

    sample_res = execute_query_sync(build_sample_query(fqn), table_id)
    if sample_res.success and sample_res.rows:
        result.sample_size = len(sample_res.rows)
        result.sample_data = [_make_json_safe(dict(zip(sample_res.columns, row, strict=False))) for row in sample_res.rows[:50]]

    columns_meta = _fetch_all(build_column_metadata_query(catalog, schema, table), table_id)
    result.column_count = len(columns_meta)

    # Combined stats query execution
    precomputed_metrics = {}
    if result.row_count > 0 and columns_meta:
        combined_query = build_combined_profiling_query(fqn, columns_meta)
        if combined_query:
            try:
                res = execute_with_timeout(combined_query, table_id)
                if res.success and res.rows:
                    row_dict = dict(zip(res.columns, res.rows[0]))
                    precomputed_metrics = parse_combined_result(columns_meta, row_dict, result.row_count)
                    logger.info("[ProfilingEngine] Combined profiling query succeeded.")
                else:
                    logger.warning("[ProfilingEngine] Combined profiling query failed or returned no rows: %s", res.error_message)
            except Exception as e:
                logger.warning("[ProfilingEngine] Combined profiling query failed, falling back to individual: %s", e)

    col_stats: list[ColumnStats] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(30, len(columns_meta) or 1)) as worker_executor:
        futures = {worker_executor.submit(_analyze_column, fqn, table_id, col[0], col[1], result.row_count, precomputed_metrics.get(col[0]), result.sample_data): col[:2] for col in columns_meta}
        for future in concurrent.futures.as_completed(futures):
            try:
                col_name, data_type = futures[future]
            except Exception as e:
                print(f"[ProfilingEngine] Error: {e}")
            try:
                col_stats.append(future.result())
            except Exception as exc:
                logger.error("[ProfilingEngine] Column %s failed: %s", col_name, exc)
                col_stats.append(ColumnStats(column_name=col_name, data_type=data_type, errors=[str(exc)]))
    result.column_stats = col_stats
    if col_stats:
        result.null_rate_avg = round(sum(c.null_rate for c in col_stats) / len(col_stats), 4)

    # Auto Insights...
    insights = [f"~{result.row_count:,} rows (COUNT(*))."] if result.row_count else []
    if result.sample_size: insights.append(f"{result.sample_size:,} rows sampled.")

    for flag, name in [("is_categorical", "categorical"), ("is_time", "Time"), ("is_geo", "Geographic")]:
        cols = [c.column_name for c in col_stats if getattr(c, flag)]
        if cols: insights.append(f"{name} columns: {', '.join(cols[:5])}.")

    high_null = [c.column_name for c in col_stats if c.null_rate > 0.20]
    if high_null: insights.append(f"High null rate (>20%): {', '.join(high_null[:5])}.")

    if result.row_count > 0:
        pk_candidates = [c.column_name for c in col_stats if c.distinct_count >= result.row_count * 0.95]
        if pk_candidates: insights.append(f"PK candidates: {', '.join(pk_candidates[:3])}.")

    result.auto_insights = insights

    # Profile JSON construction
    result.profile_json = _make_json_safe({
        "table": fqn, "version": version, "computed_at": computed_at.isoformat(),
        "row_count": result.row_count, "sample_size": result.sample_size,
        "column_count": result.column_count, "null_rate_avg": result.null_rate_avg,
        "columns": [{
            "name": c.column_name, "data_type": c.data_type, "semantic_type": c.semantic_type,
            "is_categorical": c.is_categorical, "is_large_categorical": c.is_large_categorical,
            "is_free_text": c.is_free_text, "is_boolean": c.is_boolean, "is_time": c.is_time,
            "is_geo": c.is_geo, "is_continuous": c.is_continuous, "distinct_count": c.distinct_count,
            "null_rate": c.null_rate, "stats": c.stats_json
        } for c in col_stats],
        "insights": insights, "errors": result.errors,
    })

    result.sample_data = _make_json_safe(result.sample_data)
    for c in col_stats: c.stats_json = _make_json_safe(c.stats_json)

    result.success = result.row_count > 0 or not result.errors
    logger.info("[ProfilingEngine] Done: %s — %d cols", fqn, len(col_stats))
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
        semantic = col_ctx["semantic_type"]

        # Whitelist safe keys directly into the context object to prevent nesting boilerplate
        for key in ["values", "examples", "min", "max", "avg", "distinct_count"]:
            if key in stats: col_ctx[key] = stats[key]

        context_columns.append(col_ctx)

    return {
        "table": table_name,
        "row_count": (profile_json or {}).get("row_count"),
        "columns": context_columns,
        "insights": (profile_json or {}).get("insights", []),
    }


# ── One-time LLM Table Summarization (called during profiling) ─────────────────


def generate_table_summary(result: "TableProfilingResult") -> str:
    """Uses LLM to summarize the entire table based on JSON profile."""
    

    try:
        col_lines = []
        for c in result.column_stats[:30]:
            parts = [f"{c.column_name} ({c.data_type}, {c.semantic_type})"]
            if c.is_categorical and c.top_values:
                vals = [str(v["value"]) for v in c.top_values[:5]]
                parts.append(f"values: {', '.join(vals)}")
            elif c.min_value or c.max_value:
                parts.append(f"range: {c.min_value}-{c.max_value}")
            col_lines.append(" — ".join(parts))

        prompt = (
            f"Table: {result.table_fqn}\n"
            f"Row count: {result.row_count:,}\n"
            f"Columns ({result.column_count} total):\n"
            + "\n".join(f"  • {line}" for line in col_lines)
            + "\n\nWrite a concise ≤3-sentence description of this table's purpose, "
            "what business domain it represents, and which columns are most important "
            "for querying. Be specific about what the table contains."
        )

        llm = ChatOpenAI(
            model=settings.LLM_MODEL,
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            temperature=0.0,
            timeout=60.0
        )

        response = llm.invoke(prompt)
        return response.content

    except Exception as exc:
        logger.warning("[ProfilingEngine] generate_table_summary failed: %s", exc)
        return ""
