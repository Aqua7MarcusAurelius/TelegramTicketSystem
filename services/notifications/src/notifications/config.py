"""Конфиг notifications."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.config import OptionalInt, OptionalStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_dsn: str = Field(alias="POSTGRES_DSN")
    redis_url: str = Field(alias="REDIS_URL")

    executor_group_chat_id: OptionalInt = Field(default=None, alias="EXECUTOR_GROUP_CHAT_ID")
    executor_group_topic_incoming: OptionalInt = Field(
        default=None, alias="EXECUTOR_GROUP_TOPIC_INCOMING"
    )
    executor_group_topic_digest: OptionalInt = Field(
        default=None, alias="EXECUTOR_GROUP_TOPIC_DIGEST"
    )
    executor_group_topic_logs: OptionalInt = Field(
        default=None, alias="EXECUTOR_GROUP_TOPIC_LOGS"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: OptionalStr = Field(default=None, alias="SENTRY_DSN")
