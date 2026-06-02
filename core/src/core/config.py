from pydantic_settings import BaseSettings, SettingsConfigDict

class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./text2sql.db"

    TRINO_HOST: str = "localhost"
    TRINO_PORT: int = 8080
    TRINO_USER: str = "trino"
    TRINO_PASSWORD: str = ""
    TRINO_CATALOG: str = "minio"
    TRINO_SCHEMA: str = "simple_retail"
    TRINO_HTTP_SCHEME: str = "http"
    TRINO_REQUEST_TIMEOUT: float = 30.0
    TRINO_ENABLED: bool = True
    TRINO_VERIFY: bool | str = False
    TRINO_CERT_PATH: str | None = None
    TRINO_KEY_PATH: str | None = None

settings = CoreSettings()
