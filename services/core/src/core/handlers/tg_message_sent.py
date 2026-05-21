"""Подписчик на ``events.tg.message_sent``. Spec 002 фаза 3."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgMessageSent
from shared.events.dispatch import stream_for
from shared.events.streams import TG_MESSAGE_SENT
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import (
    HandleHeaderMessageSent,
    TicketResult,
)

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=TG_MESSAGE_SENT, group="core")
    async def on_message_sent(event: TgMessageSent) -> None:
        async with session_factory() as session:
            use_case = HandleHeaderMessageSent(
                tickets=TicketsRepository(session),
                customers=CustomersRepository(session),
                processed=ProcessedEventsRepository(session),
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, TicketResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            for ev in result.events:
                await broker.publish(ev, stream=stream_for(ev))
            return
        log.debug("message_sent_skipped", reason=result.reason, event_id=event.event_id)
