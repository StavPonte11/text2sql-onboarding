from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    LLM_API_KEY: str = "ollama"
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gemma4:e4b"
    EMBEDDER_URL: str = "http://localhost:11434"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"
    HYBRID_SEARCH_MAX_TABLES: int = 10
    MAX_PROFILES_TO_FETCH: int = 3
    PROFILE_FETCH_CONCURRENCY: int = Field(default=4, gt=0)
    REDIS_URL: str = "redis://localhost:6379"

    LANGFUSE_SECRET_KEY: str = Field(min_length=1)
    LANGFUSE_PUBLIC_KEY: str = Field(min_length=1)
    LANGFUSE_BASE_URL: str = Field(min_length=1)

    # ── Jeen Integration ──────────────────────────────────────────────────────
    JEEN_LLM_CORE_URL: str = ""  # If empty, agent gracefully skips fetching
    JEEN_API_KEY: str = ""       # If empty, agent gracefully skips fetching
    SKILLS_HOT_RELOAD: bool = False  # If true, bypass Redis cache for skills

    # ── G4: Feature Flags & Execution Modes ──────────────────────────────────
    BACKEND_URL: str = ""  # Studio backend URL for flag reads (e.g. http://backend:8000)
                           # If empty, FlagBridge falls back to env-var defaults


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

    # ── G2-01: Table Scoping ──────────────────────────────────────────────────
    TABLE_SCOPING_MODE: Literal["strict", "hybrid"] = "hybrid"

    # ── G2-03: Advanced Schema Explorer phases ────────────────────────────────
    ENABLE_SEMANTIC_TYPING: bool = False
    ENABLE_JOIN_GRAPH: bool = False
    ENABLE_SCHEMA_SUMMARIZATION: bool = False
    ENABLE_AMBIGUITY_DETECT: bool = True

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
    SCHEMA_CACHE_TTL: int = 600    # seconds — DDL content
    PROFILE_CACHE_TTL: int = 1800  # seconds — table profile statistics


settings = AgentSettings()
