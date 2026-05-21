"""Конфиг scheduler."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_dsn: str = Field(alias="POSTGRES_DSN")
    redis_url: str = Field(alias="REDIS_URL")

    digest_cron: str = Field(default="0 9 * * *", alias="DIGEST_CRON")
    tz: str = Field(default="Europe/Moscow", alias="TZ")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
