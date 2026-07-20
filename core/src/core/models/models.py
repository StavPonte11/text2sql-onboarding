import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import JSON, Column, ForeignKey
from sqlmodel import Field, SQLModel, Relationship
from pgvector.sqlalchemy import Vector


class TableStatus(StrEnum):
    draft = "draft"
    sandbox = "sandbox"
    verified = "verified"
    production = "production"
    degraded = "degraded"


class FeatureFlagType(StrEnum):
    bool = "bool"
    int = "int"
    float = "float"
    string = "string"
    json = "json"


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
    openmetadata_json: Any | None = Field(default=None, sa_column=Column(JSON))
    embedding: Any | None = Field(default=None, sa_column=Column(Vector(768)))
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


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
    openmetadata_json: dict | None
    created_at: datetime
    updated_at: datetime


class EnrichmentVersion(SQLModel, table=True):
    __tablename__ = "enrichment_versions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    version: int = Field(default=1)
    data: Any | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class EnrichmentCreate(SQLModel):
    data: dict


class EnrichmentRead(SQLModel):
    id: str
    table_id: str
    version: int
    data: dict | None

    created_at: datetime


class DifficultyLevel(StrEnum):
    simple = "simple"
    medium = "medium"
    complex = "complex"


class QuestionType(StrEnum):
    simple = "simple"
    complex = "complex"
    join = "join"
    geo = "geo"
    aggregate = "aggregate"
    time_series = "time_series"


class GoldenQuestion(SQLModel, table=True):
    __tablename__ = "golden_questions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.simple)
    question_type: QuestionType = Field(default=QuestionType.simple)
    coverage_tags: list[str] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class GoldenQuestionCreate(SQLModel):
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = DifficultyLevel.simple
    question_type: QuestionType = QuestionType.simple
    coverage_tags: list[str] | None = None


class GoldenQuestionRead(SQLModel):
    id: str
    table_id: str
    question: str
    expected_sql: str
    difficulty: DifficultyLevel
    question_type: QuestionType
    coverage_tags: list[str] | None
    created_at: datetime


class EvalStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    dataset_id: str | None = Field(default=None, index=True)
    score: float = Field(default=0.0)
    pass_rate: float = Field(default=0.0)
    fail_rate: float = Field(default=0.0)
    total_questions: int = Field(default=0)
    duration_seconds: float | None = None
    triggered_by: str = Field(default="user")  # "user" | "scheduler" | "system"
    status: EvalStatus = Field(default=EvalStatus.running)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    failure_breakdown: Any | None = Field(default=None, sa_column=Column(JSON))
    dimension_averages: Any | None = Field(default=None, sa_column=Column(JSON))
    regression_detected: bool = Field(default=False)
    regression_delta: float | None = None
    promotion_run_id: str | None = Field(
        default=None, index=True
    )  # set on regression runs
    created_at: datetime = Field(default_factory=datetime.now)


class EvalRunRead(SQLModel):
    id: str
    table_id: str | None
    table_name: str
    dataset_id: str | None
    score: float
    pass_rate: float
    fail_rate: float
    total_questions: int
    duration_seconds: float | None
    triggered_by: str
    status: EvalStatus
    started_at: datetime
    completed_at: datetime | None
    failure_breakdown: dict | None
    dimension_averages: dict | None
    regression_detected: bool
    regression_delta: float | None
    promotion_run_id: str | None = None
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION SCHEDULE MODELS
# ─────────────────────────────────────────────────────────────────────────────


class EvaluationSchedule(SQLModel, table=True):
    __tablename__ = "evaluation_schedules"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    dataset_id: str = Field(index=True)  # logical dataset name or table group
    table_scope: list[str] | None = Field(
        default=None, sa_column=Column(JSON)
    )  # list of table_ids
    cron_expression: str = Field(default="0 2 * * *")  # daily at 2am
    enabled: bool = Field(default=True)
    created_by: str = Field(default="user")
    created_at: datetime = Field(default_factory=datetime.now)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class EvaluationScheduleCreate(SQLModel):
    dataset_id: str
    table_scope: list[str] | None = None
    cron_expression: str = "0 2 * * *"
    enabled: bool = True
    created_by: str = "user"


class EvaluationScheduleUpdate(SQLModel):
    dataset_id: str | None = None
    table_scope: list[str] | None = None
    cron_expression: str | None = None
    enabled: bool | None = None


class EvaluationScheduleRead(SQLModel):
    id: str
    dataset_id: str
    table_scope: list[str] | None
    cron_expression: str
    enabled: bool
    created_by: str
    created_at: datetime
    last_run_at: datetime | None
    next_run_at: datetime | None


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION HISTORY METRICS
# ─────────────────────────────────────────────────────────────────────────────


class EvaluationHistoryMetric(SQLModel, table=True):
    __tablename__ = "evaluation_history_metrics"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(
        sa_column_args=[
            ForeignKey("eval_runs.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    metric_name: str  # e.g. "score", "pass_rate", "wrong_table_count"
    metric_value: float
    created_at: datetime = Field(default_factory=datetime.now)





# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION ALERT MODELS
# ─────────────────────────────────────────────────────────────────────────────


class AlertSeverity(StrEnum):
    info = "info"
    warning = "warning"
    critical = "critical"


class EvaluationAlert(SQLModel, table=True):
    __tablename__ = "evaluation_alerts"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    table_id: str | None = Field(
        default=None,
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    alert_type: str  # "regression", "failed_run", "low_score"
    severity: AlertSeverity = Field(default=AlertSeverity.warning)
    message: str
    details: Any | None = Field(default=None, sa_column=Column(JSON))
    acknowledged: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)


class EvaluationAlertRead(SQLModel):
    id: str
    run_id: str | None
    table_id: str | None
    alert_type: str
    severity: AlertSeverity
    message: str
    details: dict | None
    acknowledged: bool
    created_at: datetime





class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(
        sa_column_args=[
            ForeignKey("eval_runs.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    question_id: str = Field(
        sa_column_args=[
            ForeignKey("golden_questions.id", ondelete="CASCADE", onupdate="CASCADE")
        ]
    )
    score: float = Field(default=0.0)
    status: str  # "pass" | "fail"
    error_type: str | None = None


class EvalResultRead(SQLModel):
    id: str
    run_id: str
    question_id: str
    score: float
    status: str
    error_type: str | None


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
    table_id: str | None = Field(
        default=None,
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    user_id: str
    session_id: str | None = None
    raw_question: str
    normalized_question: str | None = None
    tables_accessed: list[str] | None = Field(default=None, sa_column=Column(JSON))

    final_sql: str | None = None
    result_row_count: int | None = None
    result_columns: list[str] | None = Field(default=None, sa_column=Column(JSON))

    execution_time_ms: int | None = None
    refiner_iterations: int | None = Field(default=0)
    status: str = Field(default="success")  # success/error/timeout
    error_message: str | None = None
    langfuse_trace_id: str | None = None
    # Trust & Explainability
    confidence_score: float | None = None
    explanation_text: str | None = None
    warnings_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class AuditQueryRead(SQLModel):
    id: str
    table_id: str | None
    user_id: str
    session_id: str | None
    raw_question: str
    normalized_question: str | None
    tables_accessed: list[str] | None

    final_sql: str | None
    result_row_count: int | None
    result_columns: list[str] | None

    execution_time_ms: int | None
    refiner_iterations: int | None
    status: str
    error_message: str | None
    langfuse_trace_id: str | None
    confidence_score: float | None
    explanation_text: str | None
    warnings_json: list[str] | None
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# PROFILING MODELS
# ─────────────────────────────────────────────────────────────────────────────


class ProfilingStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class ProfilingRun(SQLModel, table=True):
    __tablename__ = "profiling_runs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    status: ProfilingStatus = Field(default=ProfilingStatus.pending)
    error_message: str | None = None
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None


class TableProfile(SQLModel, table=True):
    __tablename__ = "table_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        unique=True,
        index=True,
    )
    row_count: int | None = None
    sample_size: int | None = None  # rows returned by TABLESAMPLE
    column_count: int | None = None
    size_bytes: int | None = None
    null_rate_avg: float | None = None
    duplicate_rate: float | None = None
    sample_data: Any | None = Field(default=None, sa_column=Column(JSON))
    auto_insights: list[str] | None = Field(default=None, sa_column=Column(JSON))
    profile_json: Any | None = Field(
        default=None, sa_column=Column(JSON)
    )  # full structured profile
    cached_until: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class TableProfileRead(SQLModel):
    id: str
    table_id: str
    status: ProfilingStatus
    is_partial: bool | None = None
    row_count: int | None
    sample_size: int | None
    column_count: int | None
    size_bytes: int | None
    null_rate_avg: float | None
    duplicate_rate: float | None
    sample_data: Any | None
    auto_insights: list[str] | None
    profile_json: Any | None
    cached_until: datetime | None
    created_at: datetime
    updated_at: datetime


class ColumnProfile(SQLModel, table=True):
    __tablename__ = "column_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    profile_id: str = Field(
        sa_column_args=[
            ForeignKey("table_profiles.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    column_name: str
    data_type: str | None = None
    null_count: int | None = None
    null_rate: float | None = None
    distinct_count: int | None = None
    min_value: str | None = None
    max_value: str | None = None
    avg_value: float | None = None
    median_value: float | None = None
    top_values: Any | None = Field(default=None, sa_column=Column(JSON))
    is_categorical: bool = Field(default=False)  # computed from cardinality + coverage
    is_geo: bool = Field(default=False)
    is_time: bool = Field(default=False)
    semantic_type: str | None = None  # categorical | continuous | time | geo
    stats_json: Any | None = Field(
        default=None, sa_column=Column(JSON)
    )  # full stats blob
    created_at: datetime = Field(default_factory=datetime.now)


class ColumnProfileRead(SQLModel):
    id: str
    table_id: str
    profile_id: str
    column_name: str
    data_type: str | None
    null_count: int | None
    null_rate: float | None
    distinct_count: int | None
    min_value: str | None
    max_value: str | None
    avg_value: float | None
    median_value: float | None
    top_values: Any | None
    is_categorical: bool
    is_geo: bool
    is_time: bool
    semantic_type: str | None
    stats_json: Any | None
    created_at: datetime


class CrossTableProfile(SQLModel, table=True):
    __tablename__ = "cross_table_profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    target_table_id: str = Field(
        sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")]
    )
    join_suggestion: str | None = None  # e.g. "source.user_id = target.id"
    match_strength: str = Field(default="weak")  # "strong" | "weak"
    common_columns: list[str] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class CrossTableProfileRead(SQLModel):
    id: str
    source_table_id: str
    target_table_id: str
    join_suggestion: str | None
    match_strength: str
    common_columns: list[str] | None
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK MODELS
# ─────────────────────────────────────────────────────────────────────────────


class FeedbackRating(StrEnum):
    positive = "positive"
    negative = "negative"


class QueryFeedback(SQLModel, table=True):
    __tablename__ = "query_feedback"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str
    query_id: str = Field(
        sa_column_args=[
            ForeignKey("audit_queries.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    table_id: str | None = Field(
        default=None,
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    rating: FeedbackRating
    comment: str | None = None
    suggested_correction: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class QueryFeedbackCreate(SQLModel):
    user_id: str
    query_id: str
    table_id: str | None = None
    rating: FeedbackRating
    comment: str | None = None
    suggested_correction: str | None = None


class QueryFeedbackRead(SQLModel):
    id: str
    user_id: str
    query_id: str
    table_id: str | None
    rating: FeedbackRating
    comment: str | None
    suggested_correction: str | None
    created_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# TABLE HEALTH MODELS
# ─────────────────────────────────────────────────────────────────────────────


class HealthStatus(StrEnum):
    good = "good"
    warning = "warning"
    critical = "critical"


class TableHealth(SQLModel, table=True):
    __tablename__ = "table_health"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        unique=True,
        index=True,
    )
    health_score: float = Field(default=0.0)  # 0.0-1.0
    health_status: HealthStatus = Field(default=HealthStatus.warning)
    eval_success_rate: float | None = None
    feedback_ratio: float | None = None  # positive / total
    data_quality_score: float | None = None
    schema_drift_flag: bool = Field(default=False)
    # Failure breakdown
    failure_wrong_table: int = Field(default=0)
    failure_wrong_sql: int = Field(default=0)
    failure_empty_result: int = Field(default=0)
    failure_execution_error: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.now)


class TableHealthRead(SQLModel):
    id: str
    table_id: str
    health_score: float
    health_status: HealthStatus
    eval_success_rate: float | None
    feedback_ratio: float | None
    data_quality_score: float | None
    schema_drift_flag: bool
    failure_wrong_table: int
    failure_wrong_sql: int
    failure_empty_result: int
    failure_execution_error: int
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY USER MODELS  (maps to security.users in the combined DB)
# ─────────────────────────────────────────────────────────────────────────────


class OrganizationMember(SQLModel, table=True):
    __tablename__ = "organization_members"
    __table_args__ = {"schema": "security"}

    user_id: str = Field(
        foreign_key="security.users.id", primary_key=True
    )
    organization_id: str = Field(
        foreign_key="security.organizations.id", primary_key=True
    )


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "security"}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    members: list["SecurityUser"] = Relationship(
        back_populates="organizations", link_model=OrganizationMember
    )


class SecurityUser(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = {"schema": "security"}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    sso_id: str | None = Field(default=None, index=True)
    provider: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    organizations: list["Organization"] = Relationship(
        back_populates="members", link_model=OrganizationMember
    )


class SecurityUserRead(SQLModel):
    id: str
    email: str
    name: str
    is_active: bool
    is_admin: bool
    provider: str | None
    created_at: datetime
    updated_at: datetime


class AuthConfigRead(SQLModel):
    ENABLE_KEYCLOAK: bool
    ENABLE_GOOGLE: bool


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM FOREIGN KEY MODELS
# ─────────────────────────────────────────────────────────────────────────────


class ForeignKeyMapping(SQLModel, table=True):
    __tablename__ = "foreign_key_mappings"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(
        sa_column_args=[
            ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")
        ],
        index=True,
    )
    source_column: str
    target_table_id: str = Field(
        sa_column_args=[ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE")]
    )
    target_column: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ForeignKeyMappingCreate(SQLModel):
    source_column: str
    target_table_id: str
    target_column: str


class ForeignKeyMappingRead(SQLModel):
    id: str
    table_id: str
    source_column: str
    target_table_id: str
    target_column: str
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# HTTP EXTRACTORS
# ─────────────────────────────────────────────────────────────────────────────

class ExtractorStatus(StrEnum):
    draft = "draft"
    sandbox = "sandbox"
    verified = "verified"
    production = "production"
    degraded = "degraded"

class HttpExtractor(SQLModel, table=True):
    __tablename__ = "http_extractors"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)
    url: str
    description: str | None = None
    status: ExtractorStatus = Field(default=ExtractorStatus.draft)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

class HttpExtractorCreate(SQLModel):
    name: str
    url: str
    description: str | None = None
    status: ExtractorStatus = ExtractorStatus.draft

class HttpExtractorRead(SQLModel):
    id: str
    name: str
    url: str
    description: str | None
    status: ExtractorStatus
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG SCHEMA: FEATURE FLAGS & EXECUTION MODES (G4)
# ─────────────────────────────────────────────────────────────────────────────


class FeatureFlag(SQLModel, table=True):
    """
    A single runtime-configurable parameter.
    Stored in the config schema so it's logically separated from app data.
    A *missing* row means "no DB override" — callers fall back to the
    AgentSettings env-var default.
    """

    __tablename__ = "feature_flags"
    __table_args__ = {"schema": "config"}

    name: str = Field(primary_key=True)
    value: Any | None = Field(default=None, sa_column=Column(JSON))
    type: FeatureFlagType = Field(description="bool | int | float | string | json")
    description: str = Field(default="")
    owner: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_modified_by: str = Field(default="")
    last_modified_at: datetime = Field(default_factory=datetime.utcnow)


class FeatureFlagRead(SQLModel):
    name: str
    value: Any | None
    type: Literal["bool", "int", "float", "string", "json"]
    description: str
    owner: str
    created_at: datetime
    last_modified_by: str
    last_modified_at: datetime


class FeatureFlagUpdate(SQLModel):
    value: Any


class FeatureFlagAuditLog(SQLModel, table=True):
    """Immutable audit trail for every flag mutation."""

    __tablename__ = "feature_flag_audit_log"
    __table_args__ = {"schema": "config"}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    flag_name: str = Field(index=True)
    actor: str
    old_value: Any | None = Field(default=None, sa_column=Column(JSON))
    new_value: Any | None = Field(default=None, sa_column=Column(JSON))
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class ExecutionMode(SQLModel, table=True):
    """
    A named set of flag overrides that DS researchers select by name
    when calling the MCP agent tool (execution_mode="cost_saving").
    """

    __tablename__ = "execution_modes"
    __table_args__ = {"schema": "config"}

    name: str = Field(primary_key=True)
    description: str = Field(default="")
    flag_overrides: Any = Field(default_factory=dict, sa_column=Column(JSON))
    is_active: bool = Field(default=True)
    created_by: str = Field(default="system")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ExecutionModeRead(SQLModel):
    name: str
    description: str
    flag_overrides: dict
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class ExecutionModeUpsert(SQLModel):
    description: str = ""
    flag_overrides: dict = Field(default_factory=dict)
    is_active: bool = True
