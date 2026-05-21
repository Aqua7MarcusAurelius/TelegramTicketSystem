"""Подписчик на ``events.tg.message_sent``.

correlation_id может указывать на:
- шапку тикета (spec 002, фаза 3) → :class:`HandleHeaderMessageSent`
- карточку во `🆕 Входящие` (spec 003) → :class:`AttachIncomingMessageId`

Выбираем по тому, какое поле тикета совпадает с correlation_id.
"""

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
from core.services.incoming_card import AttachIncomingMessageId, IncomingResult

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=TG_MESSAGE_SENT, group="core")
    async def on_message_sent(event: TgMessageSent) -> None:
        if event.correlation_id is None:
            return

        async with session_factory() as session:
            tickets = TicketsRepository(session)
            processed = ProcessedEventsRepository(session)

            # Сначала пробуем header (фаза 3 spec 002).
            by_header = await tickets.get_by_correlation(event.correlation_id)
            if by_header is not None:
                use_case = HandleHeaderMessageSent(
                    tickets=tickets,
                    customers=CustomersRepository(session),
                    processed=processed,
                )
                result_h = await use_case.execute(event)
                await session.commit()
                if isinstance(result_h, TicketResult):
                    for cmd in result_h.commands:
                        await broker.publish(cmd, stream=stream_for(cmd))
                    for ev in result_h.events:
                        await broker.publish(ev, stream=stream_for(ev))
                else:
                    log.debug(
                        "header_message_sent_skipped",
                        reason=result_h.reason,
                        event_id=event.event_id,
                    )
                return

            # Иначе — inbox-карточка (spec 003).
            by_inbox = await tickets.get_by_inbox_correlation(event.correlation_id)
            if by_inbox is not None:
                use_case_i = AttachIncomingMessageId(tickets=tickets, processed=processed)
                result_i = await use_case_i.execute(event)
                await session.commit()
                if isinstance(result_i, IncomingResult):
                    for cmd in result_i.commands:
                        await broker.publish(cmd, stream=stream_for(cmd))
                else:
                    log.debug(
                        "inbox_message_sent_skipped",
                        reason=result_i.reason,
                        event_id=event.event_id,
                    )
                return

            # Чужой correlation_id — игнорируем.
            log.debug("message_sent_unknown_correlation", event_id=event.event_id)
