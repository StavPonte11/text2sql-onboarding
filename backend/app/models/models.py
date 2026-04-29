import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Any
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON, text


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TableCreate(SQLModel):
    name: str
    schema_name: str
    owner_id: str


class TableRead(SQLModel):
    id: str
    name: str
    schema_name: str
    status: TableStatus
    owner_id: str
    created_at: datetime
    updated_at: datetime


class EnrichmentVersion(SQLModel, table=True):
    __tablename__ = "enrichment_versions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(foreign_key="tables.id", index=True)
    version: int = Field(default=1)
    data: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EnrichmentCreate(SQLModel):
    data: dict


class EnrichmentRead(SQLModel):
    id: str
    table_id: str
    version: int
    data: Optional[dict]
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
    table_id: str = Field(foreign_key="tables.id", index=True)
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
    table_id: str = Field(foreign_key="tables.id", index=True)
    score: float = Field(default=0.0)
    status: EvalStatus = Field(default=EvalStatus.running)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvalRunRead(SQLModel):
    id: str
    table_id: str
    score: float
    status: EvalStatus
    created_at: datetime


class EvalResultStatus(str, Enum):
    pass_ = "pass"
    fail = "fail"


class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="eval_runs.id", index=True)
    question_id: str = Field(foreign_key="golden_questions.id")
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
    table_id: Optional[str] = Field(default=None, foreign_key="tables.id", index=True)
    user_id: str
    session_id: Optional[str] = None
    raw_question: str
    normalized_question: Optional[str] = None
    tables_accessed: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    final_sql: Optional[str] = None
    result_row_count: Optional[int] = None
    result_columns: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
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
    tables_accessed: Optional[list[str]]
    final_sql: Optional[str]
    result_row_count: Optional[int]
    result_columns: Optional[list[str]]
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
    table_id: str = Field(foreign_key="tables.id", index=True)
    status: ProfilingStatus = Field(default=ProfilingStatus.pending)
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    size_bytes: Optional[int] = None
    null_rate_avg: Optional[float] = None        # avg null % across columns
    duplicate_rate: Optional[float] = None
    sample_data: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # list of row dicts
    auto_insights: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    cached_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TableProfileRead(SQLModel):
    id: str
    table_id: str
    status: ProfilingStatus
    row_count: Optional[int]
    column_count: Optional[int]
    size_bytes: Optional[int]
    null_rate_avg: Optional[float]
    duplicate_rate: Optional[float]
    sample_data: Optional[Any]
    auto_insights: Optional[list[str]]
    cached_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ColumnProfile(SQLModel, table=True):
    __tablename__ = "column_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(foreign_key="tables.id", index=True)
    profile_id: str = Field(foreign_key="table_profiles.id", index=True)
    column_name: str
    data_type: Optional[str] = None
    null_count: Optional[int] = None
    null_rate: Optional[float] = None
    distinct_count: Optional[int] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    avg_value: Optional[float] = None
    top_values: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # [{value, count}]
    is_geo: bool = Field(default=False)
    is_time: bool = Field(default=False)
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
    top_values: Optional[Any]
    is_geo: bool
    is_time: bool
    created_at: datetime


class CrossTableProfile(SQLModel, table=True):
    __tablename__ = "cross_table_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_table_id: str = Field(foreign_key="tables.id", index=True)
    target_table_id: str = Field(foreign_key="tables.id")
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
    query_id: str = Field(foreign_key="audit_queries.id", index=True)
    table_id: Optional[str] = Field(default=None, foreign_key="tables.id", index=True)
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
    table_id: str = Field(foreign_key="tables.id", unique=True, index=True)
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
