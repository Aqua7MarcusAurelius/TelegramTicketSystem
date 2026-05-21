"""Подписчик на ``events.ticket.created``: рассылка карточки в `🆕 Входящие`."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TicketCreated
from shared.events.dispatch import stream_for
from shared.events.streams import TICKET_CREATED
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.incoming_card import IncomingResult, PublishIncomingCard

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=TICKET_CREATED, group="core")
    async def on_ticket_created(event: TicketCreated) -> None:
        async with session_factory() as session:
            use_case = PublishIncomingCard(
                session=session,
                tickets=TicketsRepository(session),
                executors=ExecutorsRepository(session),
                customers=CustomersRepository(session),
                processed=ProcessedEventsRepository(session),
                executor_group_chat_id=settings.executor_group_chat_id,
                executor_group_topic_incoming=settings.executor_group_topic_incoming,
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, IncomingResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            return
        log.debug("ticket_created_skipped", reason=result.reason, event_id=event.event_id)
