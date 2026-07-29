from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TEMPORAL_HOST: str = "temporal:7233"
    TRINO_REQUEST_TIMEOUT: float = 600.0
    PROFILER_MAX_CONCURRENT_QUERIES: int = 10
    PROFILING_CHUNK_SIZE: int = 5
    
    # LLM config for generating table summaries
    LLM_BASE_URL: str = "http://host.docker.internal:11434/v1"
    LLM_MODEL: str = "gemma4:e4b"
    LLM_API_KEY: str | None = "ollama"
    
    ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS: int = 600

settings = Settings()
