from pydantic_settings import BaseSettings, SettingsConfigDict

class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ESCA_API_KEY: str = ""
    ESCA_URL: str = "http://localhost:7010"
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma4:e4b"

settings = AgentSettings()
