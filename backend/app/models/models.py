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


class GoldenQuestion(SQLModel, table=True):
    __tablename__ = "golden_questions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    table_id: str = Field(foreign_key="tables.id", index=True)
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.simple)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GoldenQuestionCreate(SQLModel):
    question: str
    expected_sql: str
    difficulty: DifficultyLevel = DifficultyLevel.simple


class GoldenQuestionRead(SQLModel):
    id: str
    table_id: str
    question: str
    expected_sql: str
    difficulty: DifficultyLevel
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
    status: str = Field(default="success") # success/error/timeout
    error_message: Optional[str] = None
    langfuse_trace_id: Optional[str] = None
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
    created_at: datetime
