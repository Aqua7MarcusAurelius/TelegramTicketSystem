"""Конфиг core. См. SPEC §13."""

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

    # Командная группа (может быть пустым на старте — см. SPEC §3.6)
    executor_group_chat_id: OptionalInt = Field(default=None, alias="EXECUTOR_GROUP_CHAT_ID")
    executor_group_topic_incoming: OptionalInt = Field(
        default=None, alias="EXECUTOR_GROUP_TOPIC_INCOMING"
    )
    executor_group_topic_digest: OptionalInt = Field(
        default=None, alias="EXECUTOR_GROUP_TOPIC_DIGEST"
    )
    executor_group_topic_logs: OptionalInt = Field(default=None, alias="EXECUTOR_GROUP_TOPIC_LOGS")

    # Иконки топиков (custom_emoji_id) — см. SPEC §6
    topic_icon_new: OptionalStr = Field(default=None, alias="TOPIC_ICON_NEW")
    topic_icon_in_progress: OptionalStr = Field(default=None, alias="TOPIC_ICON_IN_PROGRESS")
    topic_icon_closed: OptionalStr = Field(default=None, alias="TOPIC_ICON_CLOSED")

    executors_config_path: str = Field(
        default="/app/config/executors.yaml", alias="EXECUTORS_CONFIG_PATH"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: OptionalStr = Field(default=None, alias="SENTRY_DSN")
    tz: str = Field(default="Europe/Moscow", alias="TZ")
