"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "xauusdt-trading-platform"
    env: str = "development"
    log_level: str = "INFO"
    db_url: str = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    redis_url: str = "redis://localhost:6379/0"
    bitget_api_base: str = "https://api.bitget.com"
    timezone: str = "UTC"


settings = Settings()
