"""FastStream-подписчик на ``events.tg.callback``.

Multiplexer: callback_data c префиксом ``menu:`` уходит в HandleMenuCallback,
с ``assign:`` — в AssignTicket. Остальные пока игнорируем — spec 004 добавит
``close:``.
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgCallback
from shared.events.dispatch import stream_for
from shared.events.streams import TG_CALLBACK
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.fsm import FsmStateRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.assign_ticket import (
    ASSIGN_PREFIX,
    AssignResult,
    AssignTicket,
)
from core.services.handle_menu_callback import (
    HandleMenuCallback,
    MenuCallbackResult,
)

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=TG_CALLBACK, group="core")
    async def on_tg_callback(event: TgCallback) -> None:
        prefix = event.callback_data.split(":", 1)[0] if ":" in event.callback_data else ""

        if prefix == ASSIGN_PREFIX:
            await _handle_assign(event, session_factory, broker, settings)
            return

        await _handle_menu(event, session_factory, broker)


async def _handle_menu(
    event: TgCallback,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
) -> None:
    async with session_factory() as session:
        use_case = HandleMenuCallback(
            customers=CustomersRepository(session),
            fsm=FsmStateRepository(session),
            tickets=TicketsRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(event)
        await session.commit()

    if isinstance(result, MenuCallbackResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))
        return
    if result.answer is not None:
        await broker.publish(result.answer, stream=stream_for(result.answer))
    else:
        log.debug("menu_callback_skipped", reason=result.reason, event_id=event.event_id)


async def _handle_assign(
    event: TgCallback,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
    settings: Settings,
) -> None:
    async with session_factory() as session:
        use_case = AssignTicket(
            session=session,
            tickets=TicketsRepository(session),
            ticket_events=TicketEventsRepository(session),
            executors=ExecutorsRepository(session),
            processed=ProcessedEventsRepository(session),
            topic_icon_in_progress=settings.topic_icon_in_progress,
        )
        result = await use_case.execute(event)
        await session.commit()

    if isinstance(result, AssignResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))
        for ev in result.events:
            await broker.publish(ev, stream=stream_for(ev))
        await broker.publish(result.answer, stream=stream_for(result.answer))
        return

    await broker.publish(result.answer, stream=stream_for(result.answer))
    log.debug("assign_skipped", reason=result.reason, event_id=event.event_id)
