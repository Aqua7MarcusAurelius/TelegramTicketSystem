"""Конфиг sheets-sync."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.config import OptionalStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_dsn: str = Field(alias="POSTGRES_DSN")
    redis_url: str = Field(alias="REDIS_URL")

    google_sheets_id: OptionalStr = Field(default=None, alias="GOOGLE_SHEETS_ID")
    google_sheets_credentials_json: OptionalStr = Field(
        default=None, alias="GOOGLE_SHEETS_CREDENTIALS_JSON"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: OptionalStr = Field(default=None, alias="SENTRY_DSN")
