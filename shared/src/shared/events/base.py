"""Базовая модель события шины.

См. docs/SPEC.md §9.2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """Базовая модель всех сообщений шины (events.* и cmd.*).

    Поля required по протоколу:
    - ``event_id`` — UUID для идемпотентности.
    - ``event_version`` — версия схемы (начиная с 1). Ломающее изменение → новый namespace.
    - ``occurred_at`` — момент возникновения в источнике, UTC.
    - ``correlation_id`` — для связки команды и ответного события (например,
      ``cmd.tg.create_forum_topic`` ↔ ``events.tg.topic_created``).
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        validate_assignment=True,
    )

    event_id: UUID = Field(default_factory=uuid4)
    event_version: int = 1
    occurred_at: datetime = Field(default_factory=_now_utc)
    correlation_id: UUID | None = None
