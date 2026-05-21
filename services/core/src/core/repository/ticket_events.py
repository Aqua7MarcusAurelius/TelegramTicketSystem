"""Repository для журнала событий по тикету (``ticket_events``).

Поле ``event_type`` — свободный текст, конкретные значения:
- ``'created'`` — фаза 1 spec 002
- ``'assigned'`` — spec 003
- ``'closed'`` — spec 004
- ``'topic_attached'``, ``'header_attached'`` — внутренние, для дебага

``payload`` — JSONB, формат зависит от ``event_type``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import TicketEvent


class TicketEventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        ticket_id: int,
        event_type: str,
        actor_user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            TicketEvent(
                ticket_id=ticket_id,
                event_type=event_type,
                actor_user_id=actor_user_id,
                payload=payload or {},
            )
        )
