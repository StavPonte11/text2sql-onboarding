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
    NOMINATIM_API_KEY: str | None = None
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

    # ── Deep Agent ────────────────────────────────────────────────────────────
    DEEP_AGENT_SKILLS_DIR: str | None = None  # Override packaged skills dir (<dir>/<name>/SKILL.md)
    DEEP_AGENT_MEMORY_DIR: str | None = None  # Override packaged memory dir (must contain AGENTS.md)
    DEEP_AGENT_MAX_ROWS: int = Field(default=100, gt=0)  # Hard cap on rows returned by execute_trino_query
    DEEP_AGENT_MAX_SAMPLE_ROWS: int = Field(default=10, gt=0)  # Hard cap on rows returned by get_sample_rows
    DEEP_AGENT_TRACING_ENABLED: bool = True  # Langfuse callbacks, span metadata and run scores
    # Loop bounds & latency (see deep_agent/limits.py)
    DEEP_AGENT_MAX_MODEL_CALLS: int = Field(default=25, gt=0)      # per run; hitting it ends the run with a best-effort answer
    DEEP_AGENT_MAX_TOOL_CALLS: int = Field(default=40, gt=0)       # per run, all tools
    DEEP_AGENT_MAX_SQL_EXECUTIONS: int = Field(default=12, gt=0)   # per run, execute_trino_query
    DEEP_AGENT_RUN_TIMEOUT_S: float = Field(default=180.0, gt=0)   # wall clock per run
    DEEP_AGENT_LLM_TIMEOUT_S: float = Field(default=60.0, gt=0)    # per LLM request
    DEEP_AGENT_LLM_MAX_RETRIES: int = Field(default=2, ge=0)       # retries by ModelRetryMiddleware
    DEEP_AGENT_RECURSION_LIMIT: int = Field(default=150, gt=0)     # LangGraph super-steps per run
    JEEN_METADATA_CACHE_TTL_S: float = Field(default=300.0, ge=0)  # metadata read cache (0 disables)
    JEEN_METADATA_REUSE_SESSION: bool = True                       # keep one MCP session open per event loop
    # Prompts & subagents (see deep_agent/prompt_registry.py, deep_agent/subagents.py)
    DEEP_AGENT_LANGFUSE_PROMPTS: bool = True       # fetch deep-agent/<name> prompts from Langfuse (packaged .md fallback)
    DEEP_AGENT_PROMPT_LABEL: str = "production"    # Langfuse prompt label to serve
    DEEP_AGENT_PROMPT_CACHE_TTL_S: int = Field(default=60, ge=0)
    DEEP_AGENT_SUBAGENT_MODEL: str | None = None    # smaller/faster model for subagents (default: main model)
    DEEP_AGENT_SUBAGENT_MAX_TOOL_CALLS: int = Field(default=15, gt=0)
    # Utils-Server tools via Jarvis Registry MCP gateway (see deep_agent/utils_mcp.py); unset URL disables
    JARVIS_GATEWAY_URL: str | None = None           # e.g. http://jarvis-mcp-gateway:8001
    JARVIS_MCP_NAMESPACE: str = "global"            # gateway namespace (provider slug) exposing Utils-Server
    JARVIS_API_KEY: str = ""                        # sent as "Authorization: ApiKey <key>"
    UTILS_MCP_TIMEOUT_S: float = Field(default=10.0, gt=0)
    # location-analyst subagent (see deep_agent/geo/)
    GEO_CANDIDATE_LIMIT: int = Field(default=5, gt=0)                 # geocoder candidates compared per lookup
    GEO_AMBIGUITY_DISTANCE_KM: float = Field(default=5.0, gt=0)       # similarly-ranked candidates farther apart = ambiguous
    GEO_AMBIGUITY_IMPORTANCE_RATIO: float = Field(default=0.8, gt=0, le=1)
    GEO_MAX_WKT_CHARS: int = Field(default=100_000, gt=0)             # registry geometry size (never in prompts; Trino query.max-length is 1M)
    GEO_MAX_SIMPLIFY_TOLERANCE_M: float = Field(default=100.0, gt=0)  # larger simplification would change matches -> error
    GEO_PROXIMITY_DEFAULTS_M: dict[str, float] = {}                  # per entity_type "near" radius; empty = ask the user
    # Serving (see deep_agent/service.py)
    AGENT_ENGINE: Literal["legacy", "deep"] = "legacy"   # default engine for chat_with_agent (per-call `engine` overrides)
    DEEP_AGENT_CHECKPOINT_DB_URL: str | None = None       # postgresql://... for durable deep-agent threads; unset = in-memory

    # ── Trino connection info for explicit catalog/schema qualification ──
    TRINO_CATALOG: str = ""
    TRINO_SCHEMA: str = ""

    # ── G4: Feature Flags & Execution Modes ──────────────────────────────────
    BACKEND_URL: str = "http://localhost:8000"  # Studio backend URL
    REDIS_URL: str = "redis://localhost:6379"

    # Langfuse prompt names
    LANGFUSE_PROMPT_SCHEMA_EXPLORER: str = "text2sql/schema_explorer"
    LANGFUSE_PROMPT_QUERY_BUILDER: str = "text2sql/query_builder"
    LANGFUSE_PROMPT_REJECTION_ROUTER: str = "text2sql/rejection_router"

    LANGFUSE_PROMPT_EXTRACTOR: str = "text2sql/enrichment_extractor"
    LANGFUSE_PROMPT_LOC_EXTRACTOR: str = "text2sql/loc_extractor"
    LANGFUSE_PROMPT_LOC_EXTRACTOR_INSTRUCTION: str = (
        "text2sql/location_wkt_instruction"
    )
    
    LANGFUSE_PROMPT_REFINER: str = "text2sql/refiner_step2"
    LANGFUSE_PROMPT_CATEGORY_ENRICHMENT: str = "text2sql/category_enrichment"
    LANGFUSE_PROMPT_DETECT_AMBIGUITY: str = "text2sql/detect_ambiguity"

    LANGFUSE_PROMPT_FINALIZER: str = "text2sql/finalizer"

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
    ENABLE_AMBIGUITY_DETECT: bool = False
    MAX_AMBIGUITY_RETRIES: int = 2  # max times user can clarify before hard stop

    # ── G2-05: Redis Schema Cache ─────────────────────────────────────────────
    SCHEMA_CACHE_TTL: int = Field(default=600, gt=0)    # seconds — DDL content


settings = AgentSettings()
