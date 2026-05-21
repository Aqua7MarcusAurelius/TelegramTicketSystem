"""Подписчик на ``events.tg.message_sent``.

correlation_id может указывать на:
- шапку тикета (spec 002, фаза 3) → :class:`HandleHeaderMessageSent`
- карточку во `🆕 Входящие` (spec 003) → :class:`AttachIncomingMessageId`
- меню при онбординге (spec 005) → :class:`HandleMenuMessageSent`

Выбираем по тому, какое поле в БД совпадает с correlation_id.
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.bus import stream_sub
from shared.events import TgMessageSent
from shared.events.dispatch import stream_for
from shared.events.streams import TG_MESSAGE_SENT
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import HandleHeaderMessageSent, TicketResult
from core.services.incoming_card import AttachIncomingMessageId, IncomingResult
from core.services.onboard_customer import HandleMenuMessageSent, OnboardResult

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=stream_sub(TG_MESSAGE_SENT, group="core"))
    async def on_message_sent(event: TgMessageSent) -> None:
        if event.correlation_id is None:
            return

        async with session_factory() as session:
            tickets = TicketsRepository(session)
            customers = CustomersRepository(session)
            processed = ProcessedEventsRepository(session)

            # 1) Шапка тикета (spec 002 phase 3)
            if (await tickets.get_by_correlation(event.correlation_id)) is not None:
                result_h = await HandleHeaderMessageSent(
                    tickets=tickets,
                    customers=customers,
                    processed=processed,
                ).execute(event)
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

            # 2) Inbox-карточка (spec 003)
            if (await tickets.get_by_inbox_correlation(event.correlation_id)) is not None:
                result_i = await AttachIncomingMessageId(
                    tickets=tickets, processed=processed
                ).execute(event)
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

            # 3) Меню онбординга (spec 005)
            if (await customers.get_by_menu_correlation(event.correlation_id)) is not None:
                result_m = await HandleMenuMessageSent(
                    customers=customers, processed=processed
                ).execute(event)
                await session.commit()
                if isinstance(result_m, OnboardResult):
                    for cmd in result_m.commands:
                        await broker.publish(cmd, stream=stream_for(cmd))
                else:
                    log.debug(
                        "menu_message_sent_skipped",
                        reason=result_m.reason,
                        event_id=event.event_id,
                    )
                return

            # Чужой correlation_id — игнорируем.
            log.debug("message_sent_unknown_correlation", event_id=event.event_id)
