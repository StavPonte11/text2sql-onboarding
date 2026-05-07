from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./text2sql.db"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    APP_ENV: str = "development"

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


settings = Settings()
