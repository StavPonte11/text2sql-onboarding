from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from python_core_utils.auth.config import SSOSettings


class AuthSettings(SSOSettings):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./text2sql.db"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8080", "http://host.docker.internal:5173"]

    # Agent MCP service URL (internal service-to-service)
    AGENT_URL: str = "http://localhost:8001"
    EVALUATION_SERVICE_URL: str = "http://localhost:8001"
    OPENMETADATA_TOKEN: str = ""

    APP_ENV: str = "development"
    OPENMETADATA_URL: str = "http://localhost:8585"
    OPENMETADATA_SERVICE_NAME: str = "local_trino"
    RUN_SEED: bool = True
    RUN_INFRA_INIT: bool = True
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

    # Starburst Galaxy Configuration
    USE_GALAXY: bool = False
    GALAXY_HOST: str = ""
    GALAXY_PORT: int = 443
    GALAXY_USERNAME: str = ""
    GALAXY_PASSWORD: str = ""
    GALAXY_HTTP_SCHEME: str = "https"

    @model_validator(mode="after")
    def override_trino_settings(self) -> "Settings":
        if self.USE_GALAXY:
            if self.GALAXY_HOST:
                self.TRINO_HOST = self.GALAXY_HOST
            if self.GALAXY_PORT:
                self.TRINO_PORT = self.GALAXY_PORT
            if self.GALAXY_USERNAME:
                self.TRINO_USER = self.GALAXY_USERNAME
            if self.GALAXY_PASSWORD:
                self.TRINO_PASSWORD = self.GALAXY_PASSWORD
            if self.GALAXY_HTTP_SCHEME:
                self.TRINO_HTTP_SCHEME = self.GALAXY_HTTP_SCHEME
            self.TRINO_CATALOG = "spider2_airlines"
            self.TRINO_SCHEMA = "airlines"
            self.TRINO_VERIFY = True
            # Also point OpenMetadata service name to galaxy cluster if needed
            self.OPENMETADATA_SERVICE_NAME = "galaxy_trino"
        return self


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
    EMBEDDER_URL: str = "http://host.docker.internal:11434/v1/embeddings"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"
    EMBEDDER_KEY: str = ""


settings = Settings()
auth_settings = AuthSettings()
