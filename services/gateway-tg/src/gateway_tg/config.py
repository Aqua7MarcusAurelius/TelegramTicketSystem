"""Конфиг gateway-tg. См. SPEC §13."""

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

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_use_webhook: bool = Field(default=False, alias="BOT_USE_WEBHOOK")
    bot_webhook_url: OptionalStr = Field(default=None, alias="BOT_WEBHOOK_URL")
    bot_webhook_secret: OptionalStr = Field(default=None, alias="BOT_WEBHOOK_SECRET")

    redis_url: str = Field(alias="REDIS_URL")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: OptionalStr = Field(default=None, alias="SENTRY_DSN")
