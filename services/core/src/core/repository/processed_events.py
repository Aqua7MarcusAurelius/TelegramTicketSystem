"""Идемпотентность обработки сообщений шины.

SPEC §9.1: каждый сервис ведёт свою таблицу ``processed_events``. Если событие с
данным ``event_id`` уже видели — пропускаем.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import ProcessedEvent


class ProcessedEventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_mark(self, event_id: UUID) -> bool:
        """Атомарно зарегистрировать событие.

        Возвращает ``True``, если запись создана впервые (это первый видим
        этого ``event_id``), ``False`` — если событие уже было обработано.

        Использует ``ON CONFLICT DO NOTHING`` — гонки между конкурентными
        обработчиками разрешает БД.
        """

        stmt = (
            pg_insert(ProcessedEvent)
            .values(event_id=event_id)
            .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
            .returning(ProcessedEvent.event_id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        return inserted is not None
