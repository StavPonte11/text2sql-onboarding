from pydantic import model_validator
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

    # Starburst Galaxy Configuration
    USE_GALAXY: bool = False
    GALAXY_HOST: str = ""
    GALAXY_PORT: int = 443
    GALAXY_USERNAME: str = ""
    GALAXY_PASSWORD: str = ""
    GALAXY_HTTP_SCHEME: str = "https"

    @model_validator(mode="after")
    def override_trino_settings(self) -> "CoreSettings":
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
        return self

settings = CoreSettings()

