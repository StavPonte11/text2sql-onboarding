from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

# Reload trigger comment (timeout added)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    ESCA_WRITE_ENABLED: bool = False
    PREVIEW_ROWS_COUNT: int | None = None
    LLM_API_KEY: str = "ollama"
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gemma4:e4b"
    EMBEDDER_URL: str = "http://localhost:11434/v1/embeddings"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"
    EMBEDDER_KEY: str = ""
    HYBRID_SEARCH_MAX_TABLES: int = 10
    LANGFUSE_SECRET_KEY: str = Field(min_length=1)
    LANGFUSE_PUBLIC_KEY: str = Field(min_length=1)
    LANGFUSE_BASE_URL: str = Field(min_length=1)
    CACHE_KEY_DELIMITER: str = "#@#"

    # ── Jeen Integration ──────────────────────────────────────────────────────
    JEEN_LLM_CORE_URL: str = "http://schema-modeler.dev161.internal/api/mcp"  # If empty, agent gracefully skips fetching
    JEEN_API_KEY: str = "mcp_ecd023ab04f849b36aef5d797525365c4c095052e6e07577d065bfe82507ae67"       # If empty, agent gracefully skips fetching
    SKILLS_HOT_RELOAD: bool = False  # If true, bypass Redis cache for skills
    NOMINATIM_USER_AGENT: str = "text2sql-agent/1.0"  # Nominatim acceptable-use identifier
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
    NOMINATIM_TIMEOUT: int = Field(default=10, gt=0)  # seconds
    NOMINATIM_RATE_LIMIT_SECONDS: float = Field(default=1.0, gt=0)  # min gap between requests
    NOMINATIM_SIMPLIFY_ITERATIONS: int = Field(default=25, gt=0)  # binary-search WKT simplify steps
    LOCATION_MAX_WKT_LENGTH: int = Field(default=2100, gt=0)  # max chars for WKT polygon string

    # ── Jeen Metadata MCP Integration ─────────────────────────────────────────
    # When set, schema_explorer pulls tables/profiles from jeen-metadata via MCP
    # instead of from the local Postgres DB.  Leave empty to keep the local path.
    JEEN_METADATA_MCP_URL: str = "http://schema-modeler.dev161.internal/api/mcp"          # e.g. https://jeen-metadata.example.com/api/mcp
    JEEN_METADATA_MCP_KEY: str = "mcp_f885337e381366db5edc22093415450e38f71e997e96dc708fea69bde9529ab9"          # Bearer key from /api/mcp/keys in jeen-metadata
    JEEN_METADATA_CONNECTION_ID: int = 89     # Numeric service ID (from list_connections)
    JEEN_METADATA_SEARCH_LIMIT: int = 10     # Max tables returned by the search tool
    JEEN_METADATA_PROFILE_TIMEOUT: float = 30.0  # Per-MCP-call timeout (seconds)

    # ── Trino connection info for explicit catalog/schema qualification ──
    TRINO_CATALOG: str = ""
    TRINO_SCHEMA: str = ""

    # ── G4: Feature Flags & Execution Modes ──────────────────────────────────
    BACKEND_URL: str = "http://localhost:8000"  # Studio backend URL
    REDIS_URL: str = "redis://localhost:6379"

    # Langfuse prompt names
    LANGFUSE_PROMPT_EXTRACTOR: str = "text2sql/enrichment_extractor"
    LANGFUSE_PROMPT_SCHEMA_EXPLORER: str = "text2sql/schema_explorer"
    LANGFUSE_PROMPT_QUERY_BUILDER: str = "text2sql/query_builder"
    LANGFUSE_PROMPT_FINALIZER: str = "text2sql/finalizer"
    LANGFUSE_PROMPT_REJECTION_ROUTER: str = "text2sql/rejection_router"
    LANGFUSE_PROMPT_CATEGORY_ENRICHMENT: str = "text2sql/category_enrichment"
    LANGFUSE_PROMPT_LOC_EXTRACTOR: str = "text2sql/loc_extractor"
    LANGFUSE_PROMPT_LOC_EXTRACTOR_INSTRUCTION: str = (
        "text2sql/location_wkt_instruction"
    )
    LANGFUSE_PROMPT_REFINER_STEP2: str = "text2sql/refiner_step2"
    LANGFUSE_PROMPT_DETECT_AMBIGUITY: str = "text2sql/detect_ambiguity"

    MAX_REFINER_ITERATIONS: int = Field(default=20, gt=0)
    REFINER_SCHEMA_CONTEXT_TABLES: int = Field(default=8, gt=0)

    # ── G2-01: Table Scoping ──────────────────────────────────────────────────
    DEFAULT_TABLE_SCOPING_MODE: Literal["strict", "hybrid"] = "hybrid"

    ENABLE_SKILL_INJECTION: bool = False

    # ── G2-04: Satisfaction Check ─────────────────────────────────────────────
    SATISFACTION_CHECK_ENABLED: bool = True
    SATISFACTION_CHECK_EXECUTION: bool = True
    SATISFACTION_CHECK_PLAUSIBILITY: bool = True
    SATISFACTION_CHECK_COLUMNS: bool = False
    SATISFACTION_CHECK_SEMANTIC: bool = False  # LLM-heavy, off by default
    SATISFACTION_MIN_ROWS: int = 1
    SATISFACTION_MAX_ROWS: int = 50_000
    SATISFACTION_SEMANTIC_THRESHOLD: float = 0.75
    SATISFACTION_MAX_FAILURES: int = 2  # escalate to HITL after this many check failures

    # ── Ambiguity Resolution ──────────────────────────────────────────────────
    ENABLE_AMBIGUITY_DETECT: bool = True
    MAX_AMBIGUITY_RETRIES: int = 2  # max times user can clarify before hard stop

    # ── G2-05: Redis Schema Cache ─────────────────────────────────────────────
    SCHEMA_CACHE_TTL: int = Field(default=600, gt=0)    # seconds — DDL content


settings = AgentSettings()
