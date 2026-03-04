from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderExecutionConfig(BaseModel):
    provider_priority: list[str] = Field(default_factory=lambda: ["openai", "anthropic"])
    timeout_seconds: float = 20.0
    retry_limit: int = 2
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_reset_seconds: int = 60


class LLMExecutionConfig(BaseModel):
    provider: ProviderExecutionConfig = Field(default_factory=ProviderExecutionConfig)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Multi-Agent Knowledge Sharing MCP"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    db_name: str = Field(default="multi_agent", alias="DB_NAME")
    db_user: str = Field(default="postgres", alias="DB_USER")
    db_password: str = Field(default="postgres", alias="DB_PASSWORD")
    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")

    auth_header_name: str = "x-api-key"

    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    embed_provider: str = Field(default="openai", alias="EMBED_PROVIDER")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    provider_priority: list[str] = Field(default_factory=lambda: ["openai", "anthropic"], alias="PROVIDER_PRIORITY")
    provider_timeout_seconds: float = Field(default=20.0, alias="PROVIDER_TIMEOUT_SECONDS")
    provider_retry_limit: int = Field(default=2, alias="PROVIDER_RETRY_LIMIT")
    provider_cb_failure_threshold: int = Field(default=3, alias="PROVIDER_CB_FAILURE_THRESHOLD")
    provider_cb_reset_seconds: int = Field(default=60, alias="PROVIDER_CB_RESET_SECONDS")

    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = Field(default=1536, alias="EMBEDDING_DIM")

    default_top_k: int = 8

    @property
    def provider_execution(self) -> ProviderExecutionConfig:
        return ProviderExecutionConfig(
            provider_priority=self.provider_priority,
            timeout_seconds=self.provider_timeout_seconds,
            retry_limit=self.provider_retry_limit,
            circuit_breaker_failure_threshold=self.provider_cb_failure_threshold,
            circuit_breaker_reset_seconds=self.provider_cb_reset_seconds,
        )

    @property
    def llm_execution(self) -> LLMExecutionConfig:
        return LLMExecutionConfig(provider=self.provider_execution)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
