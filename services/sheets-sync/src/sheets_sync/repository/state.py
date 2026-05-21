"""Repository для ``sheets_sync_state`` и ``sheets_sync_processed_events``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sheets_sync.repository.models import ProcessedEvent, SheetsSyncState


class SheetsSyncStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, ticket_id: int) -> SheetsSyncState | None:
        return await self._session.get(SheetsSyncState, ticket_id)

    async def upsert(
        self,
        *,
        ticket_id: int,
        sheet_row: int,
        last_event_id: UUID,
    ) -> None:
        existing = await self._session.get(SheetsSyncState, ticket_id)
        now = datetime.now(UTC)
        if existing is None:
            self._session.add(
                SheetsSyncState(
                    ticket_id=ticket_id,
                    sheet_row=sheet_row,
                    last_synced_at=now,
                    last_event_id=last_event_id,
                )
            )
        else:
            existing.sheet_row = sheet_row
            existing.last_synced_at = now
            existing.last_event_id = last_event_id


class ProcessedEventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_mark(self, event_id: UUID) -> bool:
        stmt = (
            pg_insert(ProcessedEvent)
            .values(event_id=event_id)
            .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
            .returning(ProcessedEvent.event_id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        return inserted is not None
