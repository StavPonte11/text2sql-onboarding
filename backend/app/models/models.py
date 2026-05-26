import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Any, List, Dict

from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON, text, ForeignKey


class TableStatus(str, Enum):
    draft = "draft"
    sandbox = "sandbox"
    verified = "verified"
    production = "production"
    degraded = "degraded"


class Table(SQLModel, table=True):
    __tablename__ = "tables"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True)
    schema_name: str
    status: TableStatus = Field(default=TableStatus.draft)
    owner_id: str
    oasis_source_id: str
    catalog: str = Field(default="dataverse")
    service: str = Field(default="trino_ingestion")
    openmetadata_json: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TableCreate(SQLModel):
    oasis_source_id: str


class TableRead(SQLModel):
    id: str
    name: str
    schema_name: str
    status: TableStatus
    owner_id: str
    oasis_source_id: str
    catalog: str
    service: str
    openmetadata_json: Optional[dict]
    created_at: datetime
    updated_at: datetime


class EnrichmentVersion(SQLModel, table=True):
    __tablename__ = "enrichment_versions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    version: int = Field(default=1)
    data: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EnrichmentCreate(SQLModel):
    data: Dict



class EnrichmentRead(SQLModel):
    id: str
    table_id: str
    version: int
    data: Optional[Dict]

    created_at: datetime


class DifficultyLevel(str, Enum):
    simple = "simple"
    medium = "medium"
    complex = "complex"


class QuestionType(str, Enum):
    simple = "simple"
    complex = "complex"
    join = "join"
    geo = "geo"
    aggregate = "aggregate"
    time_series = "time_series"


class GoldenQuestion(SQLModel, table=True):
    __tablename__ = "golden_questions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.simple)
    question_type: QuestionType = Field(default=QuestionType.simple)
    coverage_tags: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GoldenQuestionCreate(SQLModel):
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = DifficultyLevel.simple
    question_type: QuestionType = QuestionType.simple
    coverage_tags: Optional[list[str]] = None


class GoldenQuestionRead(SQLModel):
    id: str
    table_id: str
    question: str
    expected_sql: str
    difficulty: DifficultyLevel
    question_type: QuestionType
    coverage_tags: Optional[list[str]]
    created_at: datetime


class EvalStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    dataset_id: Optional[str] = Field(default=None, index=True)
    score: float = Field(default=0.0)
    pass_rate: float = Field(default=0.0)
    fail_rate: float = Field(default=0.0)
    total_questions: int = Field(default=0)
    duration_seconds: Optional[float] = None
    triggered_by: str = Field(default="user")  # "user" | "scheduler" | "system"
    status: EvalStatus = Field(default=EvalStatus.running)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    failure_breakdown: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    dimension_averages: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    regression_detected: bool = Field(default=False)
    regression_delta: Optional[float] = None
    promotion_run_id: Optional[str] = Field(default=None, index=True)  # set on regression runs
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvalRunRead(SQLModel):
    id: str
    table_id: Optional[str]
    table_name: str
    dataset_id: Optional[str]
    score: float
    pass_rate: float
    fail_rate: float
    total_questions: int
    duration_seconds: Optional[float]
    triggered_by: str
    status: EvalStatus
    started_at: datetime
    completed_at: Optional[datetime]
    failure_breakdown: Optional[dict]
    dimension_averages: Optional[dict]
    regression_detected: bool
    regression_delta: Optional[float]
    promotion_run_id: Optional[str] = None
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION SCHEDULE MODELS
# ─────────────────────────────────────────────────────────────────────────────

class EvaluationSchedule(SQLModel, table=True):
    __tablename__ = "evaluation_schedules"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    dataset_id: str = Field(index=True)  # logical dataset name or table group
    table_scope: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))  # list of table_ids
    cron_expression: str = Field(default="0 2 * * *")  # daily at 2am
    enabled: bool = Field(default=True)
    created_by: str = Field(default="user")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None


class EvaluationScheduleCreate(SQLModel):
    dataset_id: str
    table_scope: Optional[list[str]] = None
    cron_expression: str = "0 2 * * *"
    enabled: bool = True
    created_by: str = "user"


class EvaluationScheduleUpdate(SQLModel):
    dataset_id: Optional[str] = None
    table_scope: Optional[list[str]] = None
    cron_expression: Optional[str] = None
    enabled: Optional[bool] = None


class EvaluationScheduleRead(SQLModel):
    id: str
    dataset_id: str
    table_scope: Optional[list[str]]
    cron_expression: str
    enabled: bool
    created_by: str
    created_at: datetime
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION HISTORY METRICS
# ─────────────────────────────────────────────────────────────────────────────

class EvaluationHistoryMetric(SQLModel, table=True):
    __tablename__ = "evaluation_history_metrics"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(sa_column_args=[ForeignKey("eval_runs.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    metric_name: str  # e.g. "score", "pass_rate", "wrong_table_count"
    metric_value: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationHistoryMetricRead(SQLModel):
    id: str
    run_id: str
    metric_name: str
    metric_value: float
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION ALERT MODELS
# ─────────────────────────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class EvaluationAlert(SQLModel, table=True):
    __tablename__ = "evaluation_alerts"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: Optional[str] = Field(default=None, index=True)
    table_id: Optional[str] = Field(default=None, sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    alert_type: str  # "regression", "failed_run", "low_score"
    severity: AlertSeverity = Field(default=AlertSeverity.warning)
    message: str
    details: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    acknowledged: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationAlertRead(SQLModel):
    id: str
    run_id: Optional[str]
    table_id: Optional[str]
    alert_type: str
    severity: AlertSeverity
    message: str
    details: Optional[dict]
    acknowledged: bool
    created_at: datetime


class EvalResultStatus(str, Enum):
    pass_ = "pass"
    fail = "fail"


class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(sa_column_args=[ForeignKey("eval_runs.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    question_id: str = Field(sa_column_args=[ForeignKey("golden_questions.id", ondelete="CASCADE", onupdate="CASCADE")])
    score: float = Field(default=0.0)
    status: str  # "pass" | "fail"
    error_type: Optional[str] = None


class EvalResultRead(SQLModel):
    id: str
    run_id: str
    question_id: str
    score: float
    status: str
    error_type: Optional[str]


class UserScope(SQLModel, table=True):
    __tablename__ = "user_scopes"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    name: str
    is_active: bool = Field(default=False)


class UserScopeCreate(SQLModel):
    user_id: str
    name: str


class UserScopeRead(SQLModel):
    id: str
    user_id: str
    name: str
    is_active: bool


class AuditQuery(SQLModel, table=True):
    __tablename__ = "audit_queries"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: Optional[str] = Field(default=None, sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    user_id: str
    session_id: Optional[str] = None
    raw_question: str
    normalized_question: Optional[str] = None
    tables_accessed: Optional[List[str]] = Field(default=None, sa_column=Column(JSON))

    final_sql: Optional[str] = None
    result_row_count: Optional[int] = None
    result_columns: Optional[List[str]] = Field(default=None, sa_column=Column(JSON))

    execution_time_ms: Optional[int] = None
    refiner_iterations: Optional[int] = Field(default=0)
    status: str = Field(default="success")  # success/error/timeout
    error_message: Optional[str] = None
    langfuse_trace_id: Optional[str] = None
    # Trust & Explainability
    confidence_score: Optional[float] = None
    explanation_text: Optional[str] = None
    warnings_json: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditQueryRead(SQLModel):
    id: str
    table_id: Optional[str]
    user_id: str
    session_id: Optional[str]
    raw_question: str
    normalized_question: Optional[str]
    tables_accessed: Optional[List[str]]

    final_sql: Optional[str]
    result_row_count: Optional[int]
    result_columns: Optional[List[str]]

    execution_time_ms: Optional[int]
    refiner_iterations: Optional[int]
    status: str
    error_message: Optional[str]
    langfuse_trace_id: Optional[str]
    confidence_score: Optional[float]
    explanation_text: Optional[str]
    warnings_json: Optional[list[str]]
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# PROFILING MODELS
# ─────────────────────────────────────────────────────────────────────────────

class ProfilingStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class TableProfile(SQLModel, table=True):
    __tablename__ = "table_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    version: int = Field(default=1)              # monotonically increasing per table
    status: ProfilingStatus = Field(default=ProfilingStatus.pending)
    row_count: Optional[int] = None
    sample_size: Optional[int] = None            # rows returned by TABLESAMPLE
    column_count: Optional[int] = None
    size_bytes: Optional[int] = None
    null_rate_avg: Optional[float] = None
    duplicate_rate: Optional[float] = None
    sample_data: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    auto_insights: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    profile_json: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # full structured profile
    cached_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TableProfileRead(SQLModel):
    id: str
    table_id: str
    version: int
    status: ProfilingStatus
    row_count: Optional[int]
    sample_size: Optional[int]
    column_count: Optional[int]
    size_bytes: Optional[int]
    null_rate_avg: Optional[float]
    duplicate_rate: Optional[float]
    sample_data: Optional[Any]
    auto_insights: Optional[list[str]]
    profile_json: Optional[Any]
    cached_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ColumnProfile(SQLModel, table=True):
    __tablename__ = "column_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    profile_id: str = Field(sa_column_args=[ForeignKey("table_profiles.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    column_name: str
    data_type: Optional[str] = None
    null_count: Optional[int] = None
    null_rate: Optional[float] = None
    distinct_count: Optional[int] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    avg_value: Optional[float] = None
    median_value: Optional[float] = None
    top_values: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    is_categorical: bool = Field(default=False)           # computed from cardinality + coverage
    is_geo: bool = Field(default=False)
    is_time: bool = Field(default=False)
    semantic_type: Optional[str] = None                   # categorical | continuous | time | geo
    stats_json: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # full stats blob
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ColumnProfileRead(SQLModel):
    id: str
    table_id: str
    profile_id: str
    column_name: str
    data_type: Optional[str]
    null_count: Optional[int]
    null_rate: Optional[float]
    distinct_count: Optional[int]
    min_value: Optional[str]
    max_value: Optional[str]
    avg_value: Optional[float]
    median_value: Optional[float]
    top_values: Optional[Any]
    is_categorical: bool
    is_geo: bool
    is_time: bool
    semantic_type: Optional[str]
    stats_json: Optional[Any]
    created_at: datetime


class CrossTableProfile(SQLModel, table=True):
    __tablename__ = "cross_table_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    target_table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")])
    join_suggestion: Optional[str] = None       # e.g. "source.user_id = target.id"
    match_strength: str = Field(default="weak") # "strong" | "weak"
    common_columns: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CrossTableProfileRead(SQLModel):
    id: str
    source_table_id: str
    target_table_id: str
    join_suggestion: Optional[str]
    match_strength: str
    common_columns: Optional[list[str]]
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK MODELS
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackRating(str, Enum):
    positive = "positive"
    negative = "negative"


class QueryFeedback(SQLModel, table=True):
    __tablename__ = "query_feedback"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str
    query_id: str = Field(sa_column_args=[ForeignKey("audit_queries.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    table_id: Optional[str] = Field(default=None, sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], index=True)
    rating: FeedbackRating
    comment: Optional[str] = None
    suggested_correction: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class QueryFeedbackCreate(SQLModel):
    user_id: str
    query_id: str
    table_id: Optional[str] = None
    rating: FeedbackRating
    comment: Optional[str] = None
    suggested_correction: Optional[str] = None


class QueryFeedbackRead(SQLModel):
    id: str
    user_id: str
    query_id: str
    table_id: Optional[str]
    rating: FeedbackRating
    comment: Optional[str]
    suggested_correction: Optional[str]
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# TABLE HEALTH MODELS
# ─────────────────────────────────────────────────────────────────────────────

class HealthStatus(str, Enum):
    good = "good"
    warning = "warning"
    critical = "critical"


class TableHealth(SQLModel, table=True):
    __tablename__ = "table_health"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")], unique=True, index=True)
    health_score: float = Field(default=0.0)        # 0.0–1.0
    health_status: HealthStatus = Field(default=HealthStatus.warning)
    eval_success_rate: Optional[float] = None
    feedback_ratio: Optional[float] = None          # positive / total
    data_quality_score: Optional[float] = None
    schema_drift_flag: bool = Field(default=False)
    # Failure breakdown
    failure_wrong_table: int = Field(default=0)
    failure_wrong_sql: int = Field(default=0)
    failure_empty_result: int = Field(default=0)
    failure_execution_error: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TableHealthRead(SQLModel):
    id: str
    table_id: str
    health_score: float
    health_status: HealthStatus
    eval_success_rate: Optional[float]
    feedback_ratio: Optional[float]
    data_quality_score: Optional[float]
    schema_drift_flag: bool
    failure_wrong_table: int
    failure_wrong_sql: int
    failure_empty_result: int
    failure_execution_error: int
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN MODELS
# ─────────────────────────────────────────────────────────────────────────────

class Admin(SQLModel, table=True):
    __tablename__ = "admins"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AdminCreate(SQLModel):
    username: str
    password: str


class AdminRead(SQLModel):
    id: str
    username: str
    created_at: datetime
