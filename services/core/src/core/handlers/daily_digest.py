"""Подписчик на ``events.schedule.daily_digest``. SPEC §11.4."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.bus import stream_sub
from shared.events import DailyDigestTick
from shared.events.dispatch import stream_for
from shared.events.streams import SCHEDULE_DAILY_DIGEST
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.repository.processed_events import ProcessedEventsRepository
from core.services.daily_digest import BuildDailyDigest, DigestResult

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=stream_sub(SCHEDULE_DAILY_DIGEST, group="core"))
    async def on_daily_digest(event: DailyDigestTick) -> None:
        async with session_factory() as session:
            use_case = BuildDailyDigest(
                session=session,
                processed=ProcessedEventsRepository(session),
                executor_group_chat_id=settings.executor_group_chat_id,
                executor_group_topic_digest=settings.executor_group_topic_digest,
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, DigestResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            return
        log.debug("daily_digest_skipped", reason=result.reason, event_id=event.event_id)
