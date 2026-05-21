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
from core.domain.onboarding import MissingRights
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.fsm import FsmStateRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.team_group import TeamGroupSetupRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.admin_commands import (
    ActivateCustomer,
    AdminResult,
    DeactivateCustomer,
    ListCustomers,
    ReloadExecutors,
    RenameCustomer,
)
from core.services.create_ticket import (
    CreateTicketPhase1,
    TicketResult,
)
from core.services.onboard_customer import (
    OnboardCustomer,
    OnboardResult,
)
from core.services.setup_team_group import (
    PrintTopicId,
    SetupTeamGroup,
    TeamGroupResult,
)

ADMIN_COMMANDS = frozenset(
    {
        "/rename_customer",
        "/deactivate_customer",
        "/activate_customer",
        "/reload_executors",
        "/list_customers",
    }
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

        # Ветка 0.5: команда /setup от исполнителя в группе заказчика — повторный
        # запуск онбординга (SPEC §3.5, spec 005).
        if (
            event.text
            and event.text.split()[0] == "/setup"
            and not event.is_service_message
            and not event.is_bot
        ):
            await _handle_setup_command(event, session_factory, broker)
            return

        # Ветка 0.6: /setup_team_group (spec 006). От исполнителя — иначе молча игнор.
        if (
            event.text
            and event.text.split()[0] == "/setup_team_group"
            and not event.is_service_message
            and not event.is_bot
        ):
            await _handle_setup_team_group(event, session_factory, broker, settings)
            return

        # Ветка 0.7: /print_topic_id — debug-утилита (spec 006). Только для исполнителей.
        if (
            event.text
            and event.text.split()[0] == "/print_topic_id"
            and not event.is_service_message
            and not event.is_bot
        ):
            await _handle_print_topic_id(event, session_factory, broker)
            return

        # Ветка 0.8: admin-команды §3.7 / spec 007. Все от исполнителя, иначе молча.
        if (
            event.text
            and event.text.split()[0] in ADMIN_COMMANDS
            and not event.is_service_message
            and not event.is_bot
        ):
            await _handle_admin_command(event, session_factory, broker, settings)
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


async def _handle_setup_command(
    event: TgMessage,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
) -> None:
    """Команда /setup — от исполнителя в группе заказчика.

    SPEC §3.7: «Команды от не-исполнителей молча игнорируются». Проверяем по
    executors-таблице.

    Известный лимит spec 005: чтобы корректно проверить текущие права бота,
    нужен ``cmd.tg.get_chat_member`` — он не реализован, поэтому для /setup
    мы предполагаем что права у бота уже есть (если их нет, ``createForumTopic`` /
    ``sendMessage`` упадёт на стороне gateway-tg, лог пойдёт в `🤖 Логи`).
    """

    async with session_factory() as session:
        # 1) Идемпотентность
        processed = ProcessedEventsRepository(session)
        if not await processed.try_mark(event.event_id):
            await session.commit()
            return

        # 2) Только исполнитель
        execs = ExecutorsRepository(session)
        actor = await execs.get_by_telegram_id(event.user_id)
        if actor is None or not actor.is_active:
            await session.commit()
            log.debug("setup_ignored_non_executor", user_id=event.user_id)
            return

        # 3) Запуск онбординга (предполагаем, что права в норме — см. docstring)
        use_case = OnboardCustomer(
            session=session,
            customers=CustomersRepository(session),
            processed=processed,
        )
        result = await use_case.from_setup_command(
            chat_id=event.chat_id,
            chat_title="Группа заказчика",  # реальное имя возьмём из cmd-результата позже
            is_forum=event.is_forum,
            rights=MissingRights(
                can_manage_topics=True,
                can_delete_messages=True,
                can_pin_messages=True,
            ),
        )
        await session.commit()

    if isinstance(result, OnboardResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))
    else:
        log.debug("setup_skipped", reason=result.reason, event_id=event.event_id)


async def _handle_setup_team_group(
    event: TgMessage,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
    settings: Settings,
) -> None:
    """Команда /setup_team_group — только от исполнителя (SPEC §3.7)."""

    async with session_factory() as session:
        processed = ProcessedEventsRepository(session)
        # Проверка идемпотентности здесь — use-case делает свой try_mark поверх,
        # вторая попытка просто вернёт already_processed.

        execs = ExecutorsRepository(session)
        actor = await execs.get_by_telegram_id(event.user_id)
        if actor is None or not actor.is_active:
            log.debug("setup_team_group_ignored_non_executor", user_id=event.user_id)
            return

        use_case = SetupTeamGroup(
            repo=TeamGroupSetupRepository(session),
            processed=processed,
            configured_chat_id=settings.executor_group_chat_id,
        )
        result = await use_case.execute(event)
        await session.commit()

    if isinstance(result, TeamGroupResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))
    else:
        log.debug("setup_team_group_skipped", reason=result.reason, event_id=event.event_id)


async def _handle_print_topic_id(
    event: TgMessage,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
) -> None:
    """``/print_topic_id`` — только исполнителям (служебная команда, SPEC §3.7)."""

    async with session_factory() as session:
        execs = ExecutorsRepository(session)
        actor = await execs.get_by_telegram_id(event.user_id)
        if actor is None or not actor.is_active:
            return

        use_case = PrintTopicId(processed=ProcessedEventsRepository(session))
        result = await use_case.execute(event)
        await session.commit()

    if isinstance(result, TeamGroupResult):
        for cmd in result.commands:
            await broker.publish(cmd, stream=stream_for(cmd))


async def _handle_admin_command(
    event: TgMessage,
    session_factory: async_sessionmaker,
    broker: RedisBroker,
    settings: Settings,
) -> None:
    """5 admin-команд §3.7. Только от активного исполнителя — иначе молча."""

    cmd = (event.text or "").split()[0]

    async with session_factory() as session:
        execs = ExecutorsRepository(session)
        actor = await execs.get_by_telegram_id(event.user_id)
        if actor is None or not actor.is_active:
            log.debug("admin_ignored_non_executor", command=cmd, user_id=event.user_id)
            return

        customers = CustomersRepository(session)
        processed = ProcessedEventsRepository(session)

        if cmd == "/rename_customer":
            result = await RenameCustomer(customers=customers, processed=processed).execute(event)
        elif cmd == "/deactivate_customer":
            result = await DeactivateCustomer(customers=customers, processed=processed).execute(
                event
            )
        elif cmd == "/activate_customer":
            result = await ActivateCustomer(customers=customers, processed=processed).execute(event)
        elif cmd == "/reload_executors":
            result = await ReloadExecutors(
                executors=execs,
                processed=processed,
                config_path=settings.executors_config_path,
            ).execute(event)
        elif cmd == "/list_customers":
            result = await ListCustomers(customers=customers, processed=processed).execute(event)
        else:
            return  # отфильтровано ADMIN_COMMANDS, защита от регрессии

        await session.commit()

    assert isinstance(result, AdminResult)
    for cmd_msg in result.commands:
        await broker.publish(cmd_msg, stream=stream_for(cmd_msg))
