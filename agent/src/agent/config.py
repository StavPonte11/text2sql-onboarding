from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma4:e4b"
    EMBEDDER_URL: str = "http://localhost:11434"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"
    HYBRID_SEARCH_MAX_TABLES: int = 10
    MAX_PROFILES_TO_FETCH: int = 3

    LANGFUSE_SECRET_KEY: str = Field(min_length=1)
    LANGFUSE_PUBLIC_KEY: str = Field(min_length=1)
    LANGFUSE_BASE_URL: str = Field(min_length=1)


settings = AgentSettings()

