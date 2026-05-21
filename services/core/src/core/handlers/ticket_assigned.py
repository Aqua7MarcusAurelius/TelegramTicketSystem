"""Подписчик на ``events.ticket.assigned``: правка карточки во `🆕 Входящие`."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TicketAssigned
from shared.events.dispatch import stream_for
from shared.events.streams import TICKET_ASSIGNED
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.incoming_card import IncomingResult, UpdateIncomingAfterAssign

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=TICKET_ASSIGNED, group="core")
    async def on_ticket_assigned(event: TicketAssigned) -> None:
        async with session_factory() as session:
            use_case = UpdateIncomingAfterAssign(
                session=session,
                tickets=TicketsRepository(session),
                customers=CustomersRepository(session),
                processed=ProcessedEventsRepository(session),
                executor_group_chat_id=settings.executor_group_chat_id,
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, IncomingResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            return
        log.debug("ticket_assigned_skipped", reason=result.reason, event_id=event.event_id)
