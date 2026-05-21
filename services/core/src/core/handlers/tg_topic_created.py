"""Подписчик на ``events.tg.topic_created``. Spec 002 фаза 2."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgTopicCreated
from shared.events.dispatch import stream_for
from shared.events.streams import TG_TOPIC_CREATED
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import (
    HandleTopicCreated,
    TicketResult,
)

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=TG_TOPIC_CREATED, group="core")
    async def on_topic_created(event: TgTopicCreated) -> None:
        async with session_factory() as session:
            use_case = HandleTopicCreated(
                tickets=TicketsRepository(session),
                customers=CustomersRepository(session),
                processed=ProcessedEventsRepository(session),
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, TicketResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            return
        log.debug("topic_created_skipped", reason=result.reason, event_id=event.event_id)
