"""Repository для ``team_group_topic_setup``. Spec 006."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import TeamGroupTopicSetup


class TeamGroupSetupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        chat_id: int,
        correlation_id: UUID,
        role: str,
    ) -> TeamGroupTopicSetup:
        row = TeamGroupTopicSetup(
            chat_id=chat_id,
            correlation_id=correlation_id,
            role=role,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_correlation(self, correlation_id: UUID) -> TeamGroupTopicSetup | None:
        stmt = select(TeamGroupTopicSetup).where(
            TeamGroupTopicSetup.correlation_id == correlation_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_chat(self, chat_id: int) -> Sequence[TeamGroupTopicSetup]:
        stmt = select(TeamGroupTopicSetup).where(TeamGroupTopicSetup.chat_id == chat_id)
        return list((await self._session.execute(stmt)).scalars())

    async def set_topic(self, correlation_id: UUID, topic_id: int) -> None:
        row = await self.get_by_correlation(correlation_id)
        if row is not None:
            row.topic_id = topic_id

    async def mark_finished(self, chat_id: int) -> None:
        now = datetime.now(UTC)
        for row in await self.list_for_chat(chat_id):
            if row.finished_at is None:
                row.finished_at = now
