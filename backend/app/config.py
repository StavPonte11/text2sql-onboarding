from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./text2sql.db"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # Agent MCP service URL (internal service-to-service)
    AGENT_URL: str = "http://localhost:8001"
    EVALUATION_SERVICE_URL: str = "http://localhost:8001"
    OPENMETADATA_TOKEN: str = ""

    APP_ENV: str = "development"
    OPENMETADATA_URL: str = "http://localhost:8585"
    OPENMETADATA_SERVICE_NAME: str = "local_trino"
    # Trino connection
    TRINO_HOST: str = "localhost"
    TRINO_PORT: int = 8080
    TRINO_USER: str = "trino"
    TRINO_PASSWORD: str = ""
    TRINO_CATALOG: str = "tpch"
    TRINO_SCHEMA: str = "tiny"
    TRINO_HTTP_SCHEME: str = "http"
    TRINO_REQUEST_TIMEOUT: float = 30.0
    TRINO_ENABLED: bool = True  # Set False to disable real Trino calls
    TRINO_VERIFY: bool | str = (
        False  # True to verify standard SSL, False to ignore, or path to cabundle.crt
    )
    TRINO_CERT_PATH: str | None = (
        None  # Path to client certificate for mTLS (.crt / .pem)
    )
    TRINO_KEY_PATH: str | None = (
        None  # Path to client private key for mTLS (.key / .pem)
    )
    TRINO_SERVICE_URL: str | None = None

    # Langfuse run-item finalization wait (used by wait_for_run_items to gate cleanup)
    # Increase LANGFUSE_WAIT_MAX_ATTEMPTS on slow private-network deployments.
    LANGFUSE_WAIT_MAX_ATTEMPTS: int = 20
    LANGFUSE_WAIT_INITIAL_DELAY_SECS: float = 0.5
    LANGFUSE_WAIT_BACKOFF_FACTOR: float = 1.5
    PROFILER_MAX_CONCURRENT_QUERIES: int = 10

    # JWT Config
    JWT_SECRET: str = "dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8

    # Embedder Config
    EMBEDDER_URL: str = "http://host.docker.internal:11434/api/embeddings"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"


settings = Settings()
