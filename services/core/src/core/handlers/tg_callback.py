"""FastStream-подписчик на ``events.tg.callback``.

Multiplexer по префиксу ``callback_data``:
- ``menu:*``               → HandleMenuCallback (spec 001)
- ``assign:*``             → AssignTicket (spec 003)
- ``close[_confirm|_cancel]:*`` → CloseTicket (spec 004)
- ``setup_recheck``        → подсказка onboarding (spec 005)
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import CmdAnswerCallbackQuery, CmdSendMessage, TgCallback
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
from core.services.close_ticket import (
    CLOSE_CANCEL_PREFIX,
    CLOSE_CONFIRM_PREFIX,
    CLOSE_PREFIX,
    CloseResult,
    CloseTicket,
)
from core.services.handle_menu_callback import (
    HandleMenuCallback,
    MenuCallbackResult,
)

CLOSE_PREFIXES = frozenset({CLOSE_PREFIX, CLOSE_CONFIRM_PREFIX, CLOSE_CANCEL_PREFIX})
SETUP_RECHECK = "setup_recheck"

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=TG_CALLBACK, group="core")
    async def on_tg_callback(event: TgCallback) -> None:
        prefix = event.callback_data.split(":", 1)[0] if ":" in event.callback_data else ""
        bare = event.callback_data.strip()

        if prefix == ASSIGN_PREFIX:
            await _handle_assign(event, session_factory, broker, settings)
            return

        if prefix in CLOSE_PREFIXES:
            await _handle_close(event, session_factory, broker, settings)
            return

        if bare == SETUP_RECHECK:
            await _handle_setup_recheck(event, broker)
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


async def _handle_close(
    event: TgCallback,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
    settings: Settings,
) -> None:
    async with session_factory() as session:
        use_case = CloseTicket(
            session=session,
            tickets=TicketsRepository(session),
            ticket_events=TicketEventsRepository(session),
            processed=ProcessedEventsRepository(session),
            topic_icon_closed=settings.topic_icon_closed,
        )
        result = await use_case.execute(event)
        await session.commit()

    if isinstance(result, CloseResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))
        for ev in result.events:
            await broker.publish(ev, stream=stream_for(ev))
        await broker.publish(result.answer, stream=stream_for(result.answer))
        return

    await broker.publish(result.answer, stream=stream_for(result.answer))
    log.debug("close_skipped", reason=result.reason, event_id=event.event_id)


async def _handle_setup_recheck(event: TgCallback, broker: RedisBroker) -> None:
    """Кнопка «🔄 Проверить ещё раз» (spec 005).

    Known limit: у нас нет ``cmd.tg.get_chat_member`` через шину, поэтому
    самостоятельно перепроверить текущие права бота use-case не может. Реальный
    ре-чек случится автоматически, когда Telegram пришлёт следующий
    ``my_chat_member`` после обновления прав. Если пользователь уже выставил
    права и хочет принудительный запуск — отправляем сообщение-инструкцию,
    /setup доделает остальное.
    """

    answer = CmdAnswerCallbackQuery(
        callback_query_id=event.callback_query_id,
        text="Обновите права бота — проверка запустится сама. Или нажмите /setup.",
        show_alert=True,
    )
    await broker.publish(answer, stream=stream_for(answer))
    hint = CmdSendMessage(
        chat_id=event.chat_id,
        text=("ℹ️ Изменение прав бота вызовет авто-проверку. Если уже сделали — отправьте /setup."),
        parse_mode="HTML",
    )
    await broker.publish(hint, stream=stream_for(hint))
