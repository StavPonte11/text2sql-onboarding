from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    LLM_API_KEY: str = "ollama"
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gemma4:e4b"
    EMBEDDER_URL: str = "http://localhost:11434"
    EMBEDDER_MODEL: str = "nomic-embed-text:latest"

    LANGFUSE_SECRET_KEY: str = Field(min_length=1)
    LANGFUSE_PUBLIC_KEY: str = Field(min_length=1)
    LANGFUSE_BASE_URL: str = Field(min_length=1)
    
    # Langfuse prompt names
    LANGFUSE_PROMPT_EXTRACTOR: str = "text2sql/extractor"
    LANGFUSE_PROMPT_SCHEMA_EXPLORER: str = "text2sql/schema_explorer"
    LANGFUSE_PROMPT_QUERY_BUILDER: str = "text2sql/query_builder"
    LANGFUSE_PROMPT_REFINER: str = "text2sql/refiner"
    LANGFUSE_PROMPT_FINALIZER_SUMMARY: str = "text2sql/finalizer_summary"
    LANGFUSE_PROMPT_FINALIZER_SQL_EXPLANATION: str = "text2sql/finalizer_sql_explanation"
    LANGFUSE_PROMPT_REJECTION_ROUTER: str = "text2sql/rejection_router"


settings = AgentSettings()
