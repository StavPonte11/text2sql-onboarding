from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

# Reload trigger comment (timeout added)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    ESCA_WRITE_ENABLED: bool = True
    LLM_API_KEY: str = "ollama"
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gemma4:e4b"
    EMBEDDER_URL: str = "http://localhost:11434/v1/embeddings"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"
    EMBEDDER_KEY: str = ""
    HYBRID_SEARCH_MAX_TABLES: int = 10
    MAX_PROFILES_TO_FETCH: int = 3
    PROFILE_FETCH_CONCURRENCY: int = Field(default=4, gt=0)
    LANGFUSE_SECRET_KEY: str = Field(min_length=1)
    LANGFUSE_PUBLIC_KEY: str = Field(min_length=1)
    LANGFUSE_BASE_URL: str = Field(min_length=1)

    # ── Jeen Integration ──────────────────────────────────────────────────────
    JEEN_LLM_CORE_URL: str = ""  # If empty, agent gracefully skips fetching
    JEEN_API_KEY: str = ""       # If empty, agent gracefully skips fetching
    SKILLS_HOT_RELOAD: bool = False  # If true, bypass Redis cache for skills

    # ── G4: Feature Flags & Execution Modes ──────────────────────────────────
    BACKEND_URL: str = "http://localhost:8000"  # Studio backend URL
    REDIS_URL: str = "redis://localhost:6379"

    # Langfuse prompt names
    LANGFUSE_PROMPT_EXTRACTOR: str = "text2sql/extractor"
    LANGFUSE_PROMPT_SCHEMA_EXPLORER: str = "text2sql/schema_explorer"
    LANGFUSE_PROMPT_QUERY_BUILDER: str = "text2sql/query_builder"
    LANGFUSE_PROMPT_REFINER: str = "text2sql/refiner"
    LANGFUSE_PROMPT_FINALIZER_SUMMARY: str = "text2sql/finalizer_summary"
    LANGFUSE_PROMPT_FINALIZER_SQL_EXPLANATION: str = (
        "text2sql/finalizer_sql_explanation"
    )
    LANGFUSE_PROMPT_REJECTION_ROUTER: str = "text2sql/rejection_router"
    LANGFUSE_PROMPT_LOC_EXTRACTOR: str = "text2sql/extractor"

    MAX_REFINER_ITERATIONS: int = Field(default=3, gt=0)
    REFINER_SCHEMA_CONTEXT_TABLES: int = Field(default=4, gt=0)

    # ── G2-01: Table Scoping ──────────────────────────────────────────────────
    DEFAULT_TABLE_SCOPING_MODE: Literal["strict", "hybrid"] = "hybrid"

    # ── G2-03: Advanced Schema Explorer phases ────────────────────────────────
    ENABLE_SEMANTIC_TYPING: bool = False   # single batched LLM call — adds id/timestamp/category labels
    ENABLE_JOIN_GRAPH: bool = False
    ENABLE_SCHEMA_SUMMARIZATION: bool = False  # generated once at profile-time, not at runtime
    ENABLE_AMBIGUITY_DETECT: bool = False
    ENABLE_SKILL_INJECTION: bool = False

    # ── G2-04: Satisfaction Check ─────────────────────────────────────────────
    SATISFACTION_CHECK_ENABLED: bool = True
    SATISFACTION_CHECK_EXECUTION: bool = True
    SATISFACTION_CHECK_PLAUSIBILITY: bool = True
    SATISFACTION_CHECK_COLUMNS: bool = True
    SATISFACTION_CHECK_SEMANTIC: bool = False  # LLM-heavy, off by default
    SATISFACTION_MIN_ROWS: int = 1
    SATISFACTION_MAX_ROWS: int = 50_000
    SATISFACTION_SEMANTIC_THRESHOLD: float = 0.75
    SATISFACTION_MAX_FAILURES: int = 2  # escalate to HITL after this many check failures

    # ── G2-05: Redis Schema Cache ─────────────────────────────────────────────
    SCHEMA_CACHE_TTL: int = Field(default=600, gt=0)    # seconds — DDL content
    PROFILE_CACHE_TTL: int = Field(default=1800, gt=0)  # seconds — table profile statistics


settings = AgentSettings()
