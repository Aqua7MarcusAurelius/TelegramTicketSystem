"""Подписчик ``events.tg.message`` — три задачи:

1. Если заказчик отвечает в General в состоянии FSM=creating_prompt — запускаем
   фазу 1 :class:`CreateTicketPhase1`.
2. Если заказчик пишет в General вне creating_prompt — удаляем сообщение
   (SPEC §7.1, spec 001 AC).
3. Если бот сам выпустил системное сообщение о открытии/закрытии General — тоже
   удаляем (SPEC §7.2: «бот чистит системные сообщения о open/close General»).
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import CmdDeleteMessage, TgMessage
from shared.events.dispatch import stream_for
from shared.events.streams import TG_MESSAGE
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.fsm import FsmStateRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import (
    CreateTicketPhase1,
    TicketResult,
)

log = structlog.get_logger(__name__)

# service_message_type'ы, которые бот сам произвёл по ходу spec 002/005.
FORUM_GENERAL_SERVICE_MESSAGES = frozenset(
    {
        "forum_topic_closed",
        "forum_topic_reopened",
        "general_forum_topic_hidden",
        "general_forum_topic_unhidden",
        "forum_topic_edited",
    }
)


def _is_general_topic(event: TgMessage) -> bool:
    """General в форум-группе — это ``topic_id is None`` ИЛИ message в нативном General."""

    return event.is_forum and event.topic_id is None


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    @broker.subscriber(stream=TG_MESSAGE, group="core")
    async def on_tg_message(event: TgMessage) -> None:
        # Ветка 0: сообщение в командной группе от исполнителя — резолвим user_id
        # (SPEC §3.4). Без commit'а будет работать lazy-резолвинг при первом сообщении.
        if (
            settings.executor_group_chat_id is not None
            and event.chat_id == settings.executor_group_chat_id
            and event.username
            and not event.is_bot
            and not event.is_service_message
        ):
            async with session_factory() as session:
                repo = ExecutorsRepository(session)
                if await repo.resolve_user_id(event.username, event.user_id):
                    await session.commit()
                    log.info(
                        "executor_user_id_resolved",
                        username=event.username,
                        user_id=event.user_id,
                    )
            # резолвинг — побочка, дальше по обычным веткам не пускаем
            return

        # Ветка 1: системное сообщение от бота (forum_topic_closed и т.п.)
        if (
            event.is_service_message
            and event.service_message_type in FORUM_GENERAL_SERVICE_MESSAGES
        ):
            # Идемпотентность для удаления — отдельный try_mark.
            async with session_factory() as session:
                processed = ProcessedEventsRepository(session)
                if not await processed.try_mark(event.event_id):
                    await session.commit()
                    return
                await session.commit()
            await broker.publish(
                CmdDeleteMessage(chat_id=event.chat_id, message_id=event.message_id),
                stream=stream_for(CmdDeleteMessage),
            )
            return

        # Дальше нас интересуют только сообщения от заказчика в General.
        if event.is_bot or event.is_anonymous_admin or event.is_service_message:
            return
        if not _is_general_topic(event):
            return

        async with session_factory() as session:
            use_case = CreateTicketPhase1(
                customers=CustomersRepository(session),
                fsm=FsmStateRepository(session),
                tickets=TicketsRepository(session),
                ticket_events=TicketEventsRepository(session),
                processed=ProcessedEventsRepository(session),
                topic_icon_new=settings.topic_icon_new,
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, TicketResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            for ev in result.events:
                await broker.publish(ev, stream=stream_for(ev))
            return

        # Skipped: либо already_processed (тогда ничего не публикуем), либо
        # заказчик пишет в General не в creating_prompt → удаляем сообщение.
        if result.reason == "already_processed":
            return
        if result.reason in {"not_creating_prompt", "empty_after_strip", "empty_text"}:
            await broker.publish(
                CmdDeleteMessage(chat_id=event.chat_id, message_id=event.message_id),
                stream=stream_for(CmdDeleteMessage),
            )
            return
        log.debug("tg_message_skipped", reason=result.reason, event_id=event.event_id)
