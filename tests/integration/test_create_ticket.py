"""Integration-тесты для spec 002 — создание тикета.

Покрывают use-case'ы:
- CreateTicketPhase1: TgMessage → ticket в БД + 4 команды.
- HandleTopicCreated: events.tg.topic_created → topic_id заполнен, шапка отправлена.
- HandleHeaderMessageSent: events.tg.message_sent → header_message_id + pin + TicketCreated.
- ExpireCreatingPrompt: тайм-аут creating_prompt → close General + меню с тостом.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from core.domain.menu import MenuState
from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository
from core.repository.models import (
    Customer,
    FsmState,
    Ticket,
    TicketEvent,
    TicketStatus,
)
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import (
    CreateTicketPhase1,
    HandleHeaderMessageSent,
    HandleTopicCreated,
    TicketResult,
    TicketSkipped,
)
from core.services.expire_creating_prompt import ExpireCreatingPrompt
from shared.events import (
    CmdCloseGeneralForumTopic,
    CmdCreateForumTopic,
    CmdDeleteMessage,
    CmdEditMessageText,
    CmdPinMessage,
    CmdSendMessage,
    TgMessage,
    TgMessageSent,
    TgTopicCreated,
    TicketCreated,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Test Customer — Тикеты"
USER_A = 11111
MENU_MESSAGE_ID = 7
ICON_NEW = "icon-emoji-id-new"


async def _seed_customer(session: AsyncSession) -> Customer:
    customer = Customer(
        telegram_chat_id=CUSTOMER_CHAT_ID,
        title=CUSTOMER_TITLE,
        menu_message_id=MENU_MESSAGE_ID,
        onboarded_at=datetime.now(UTC),
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return customer


async def _set_fsm(session: AsyncSession, state: MenuState, *, ttl: int | None = None) -> None:
    repo = FsmStateRepository(session)
    await repo.upsert(
        user_id=USER_A,
        chat_id=CUSTOMER_CHAT_ID,
        state=state,
        ttl_seconds=ttl,
    )
    await session.commit()


def _make_message(text: str, *, topic_id: int | None = None) -> TgMessage:
    return TgMessage(
        event_id=uuid4(),
        chat_id=CUSTOMER_CHAT_ID,
        chat_type="supergroup",
        is_forum=True,
        topic_id=topic_id,
        user_id=USER_A,
        username="ivan",
        full_name="Иван Тест",
        is_anonymous_admin=False,
        is_bot=False,
        is_service_message=False,
        service_message_type=None,
        text=text,
        message_id=42,
        reply_to_message_id=None,
    )


@pytest.fixture
def phase1(session: AsyncSession) -> CreateTicketPhase1:
    return CreateTicketPhase1(
        customers=CustomersRepository(session),
        fsm=FsmStateRepository(session),
        tickets=TicketsRepository(session),
        ticket_events=TicketEventsRepository(session),
        processed=ProcessedEventsRepository(session),
        topic_icon_new=ICON_NEW,
    )


class TestPhase1:
    async def test_happy_path_creates_ticket_and_emits_4_commands(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)

        event = _make_message("Поправить шапку\nКонкретно на лендинге A/B-теста")
        result = await phase1.execute(event)
        await session.commit()

        assert isinstance(result, TicketResult)
        assert len(result.commands) == 4
        types = [type(c) for c in result.commands]
        assert types == [
            CmdCreateForumTopic,
            CmdDeleteMessage,
            CmdCloseGeneralForumTopic,
            CmdEditMessageText,
        ]

        create_cmd = result.commands[0]
        assert isinstance(create_cmd, CmdCreateForumTopic)
        assert create_cmd.chat_id == CUSTOMER_CHAT_ID
        assert create_cmd.icon_custom_emoji_id == ICON_NEW
        # correlation_id будет нужен фазе 2/3 — он должен быть выставлен
        assert create_cmd.correlation_id is not None

        # Ticket в БД
        tickets = (await session.execute(select(Ticket))).scalars().all()
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.title == "Поправить шапку"
        assert ticket.description == "Конкретно на лендинге A/B-теста"
        assert ticket.status is TicketStatus.NEW
        assert ticket.topic_id is None  # фаза 2 ещё не отработала
        assert ticket.create_correlation_id == create_cmd.correlation_id
        assert ticket.created_by_user_id == USER_A

        # ticket_events ('created')
        events = (await session.execute(select(TicketEvent))).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert events[0].actor_user_id == USER_A

        # FSM → main
        fsm = await session.get(FsmState, (USER_A, CUSTOMER_CHAT_ID))
        assert fsm is not None
        assert fsm.state == MenuState.MAIN.value
        assert fsm.expires_at is None

        # Имя топика и тост
        assert create_cmd.name.startswith(f"#{ticket.id} Поправить шапку")
        edit_cmd = result.commands[3]
        assert isinstance(edit_cmd, CmdEditMessageText)
        assert f"Тикет #{ticket.id} создан" in edit_cmd.text

    async def test_not_in_creating_prompt_skipped(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        # FSM остаётся в main по дефолту
        event = _make_message("любое сообщение")
        result = await phase1.execute(event)
        await session.commit()
        assert isinstance(result, TicketSkipped)
        assert result.reason == "not_creating_prompt"
        # ticket не создан
        tickets = (await session.execute(select(Ticket))).scalars().all()
        assert tickets == []

    async def test_unknown_customer_skipped(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        # Customer'а нет
        result = await phase1.execute(_make_message("текст"))
        assert isinstance(result, TicketSkipped)
        assert result.reason == "unknown_customer"

    async def test_idempotency_on_repeat_event_id(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)

        event = _make_message("Заголовок")
        first = await phase1.execute(event)
        await session.commit()
        assert isinstance(first, TicketResult)

        second = await phase1.execute(event)
        await session.commit()
        assert isinstance(second, TicketSkipped)
        assert second.reason == "already_processed"
        # Только один тикет
        tickets = (await session.execute(select(Ticket))).scalars().all()
        assert len(tickets) == 1

    async def test_empty_text_skipped(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)
        result = await phase1.execute(_make_message("   "))
        assert isinstance(result, TicketSkipped)
        assert result.reason == "empty_text"

    async def test_menu_not_initialized_skipped(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        # Customer без menu_message_id (onboarding не завершён, spec 005)
        session.add(
            Customer(
                telegram_chat_id=CUSTOMER_CHAT_ID,
                title="X",
                menu_message_id=None,
            )
        )
        await session.commit()
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)
        result = await phase1.execute(_make_message("Заголовок"))
        assert isinstance(result, TicketSkipped)
        assert result.reason == "menu_not_initialized"


class TestPhase2TopicCreated:
    async def _phase2(self, session: AsyncSession) -> HandleTopicCreated:
        return HandleTopicCreated(
            tickets=TicketsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )

    async def test_attaches_topic_id_and_emits_header(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)
        result1 = await phase1.execute(_make_message("Тест\nописание"))
        await session.commit()
        assert isinstance(result1, TicketResult)
        create_cmd = result1.commands[0]
        assert isinstance(create_cmd, CmdCreateForumTopic)

        # Симулируем ответ от gateway-tg
        topic_event = TgTopicCreated(
            event_id=uuid4(),
            correlation_id=create_cmd.correlation_id,
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=999,
            name=create_cmd.name,
        )
        result2 = await (await self._phase2(session)).execute(topic_event)
        await session.commit()

        assert isinstance(result2, TicketResult)
        assert len(result2.commands) == 1
        send_cmd = result2.commands[0]
        assert isinstance(send_cmd, CmdSendMessage)
        assert send_cmd.chat_id == CUSTOMER_CHAT_ID
        assert send_cmd.topic_id == 999
        assert send_cmd.correlation_id == create_cmd.correlation_id
        assert "Тикет #" in send_cmd.text
        # Кнопка «Закрыть»
        assert send_cmd.reply_markup is not None
        keyboard = send_cmd.reply_markup["inline_keyboard"]
        assert keyboard[0][0]["callback_data"].startswith("close:")

        # topic_id в БД
        ticket = (await session.execute(select(Ticket))).scalar_one()
        assert ticket.topic_id == 999

    async def test_unknown_correlation_skipped(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        topic_event = TgTopicCreated(
            event_id=uuid4(),
            correlation_id=uuid4(),  # никакого тикета не соответствует
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=42,
            name="anything",
        )
        result = await (await self._phase2(session)).execute(topic_event)
        await session.commit()
        assert isinstance(result, TicketSkipped)
        assert result.reason == "unknown_correlation"

    async def test_no_correlation_skipped(self, session: AsyncSession) -> None:
        topic_event = TgTopicCreated(
            event_id=uuid4(),
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=42,
            name="x",
        )
        result = await (await self._phase2(session)).execute(topic_event)
        assert isinstance(result, TicketSkipped)
        assert result.reason == "no_correlation"


class TestPhase3MessageSent:
    async def _phase3(self, session: AsyncSession) -> HandleHeaderMessageSent:
        return HandleHeaderMessageSent(
            tickets=TicketsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )

    async def test_pins_header_and_publishes_ticket_created(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)
        result1 = await phase1.execute(_make_message("Test\nbody"))
        await session.commit()
        assert isinstance(result1, TicketResult)
        create_cmd = result1.commands[0]
        assert isinstance(create_cmd, CmdCreateForumTopic)

        # Phase 2
        topic_event = TgTopicCreated(
            event_id=uuid4(),
            correlation_id=create_cmd.correlation_id,
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=555,
            name=create_cmd.name,
        )
        phase2 = HandleTopicCreated(
            tickets=TicketsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        await phase2.execute(topic_event)
        await session.commit()

        # Phase 3 — gateway-tg отдал ответ на cmd.tg.send_message с шапкой
        sent_event = TgMessageSent(
            event_id=uuid4(),
            correlation_id=create_cmd.correlation_id,
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=555,
            message_id=12345,
        )
        result3 = await (await self._phase3(session)).execute(sent_event)
        await session.commit()

        assert isinstance(result3, TicketResult)
        # Команда — pin
        assert len(result3.commands) == 1
        pin_cmd = result3.commands[0]
        assert isinstance(pin_cmd, CmdPinMessage)
        assert pin_cmd.message_id == 12345
        # События — ticket.created
        assert len(result3.events) == 1
        created_event = result3.events[0]
        assert isinstance(created_event, TicketCreated)
        assert created_event.topic_id == 555
        assert created_event.title == "Test"
        assert created_event.description == "body"

        # header_message_id в БД
        ticket = (await session.execute(select(Ticket))).scalar_one()
        assert ticket.header_message_id == 12345

    async def test_skipped_if_topic_not_attached_yet(
        self, session: AsyncSession, phase1: CreateTicketPhase1
    ) -> None:
        await _seed_customer(session)
        await _set_fsm(session, MenuState.CREATING_PROMPT, ttl=120)
        result1 = await phase1.execute(_make_message("Test"))
        await session.commit()
        assert isinstance(result1, TicketResult)
        create_cmd = result1.commands[0]
        assert isinstance(create_cmd, CmdCreateForumTopic)

        # Сразу phase 3 без phase 2 — порядок нарушен
        sent_event = TgMessageSent(
            event_id=uuid4(),
            correlation_id=create_cmd.correlation_id,
            chat_id=CUSTOMER_CHAT_ID,
            topic_id=None,
            message_id=99,
        )
        result3 = await (await self._phase3(session)).execute(sent_event)
        await session.commit()
        assert isinstance(result3, TicketSkipped)
        assert result3.reason == "topic_not_attached_yet"


class TestExpireCreatingPrompt:
    async def _use_case(self, session: AsyncSession) -> ExpireCreatingPrompt:
        return ExpireCreatingPrompt(
            fsm=FsmStateRepository(session),
            customers=CustomersRepository(session),
        )

    async def test_returns_close_and_edit_commands_for_expired(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        # Вставим FSM с прошедшим expires_at вручную
        session.add(
            FsmState(
                user_id=USER_A,
                chat_id=CUSTOMER_CHAT_ID,
                state=MenuState.CREATING_PROMPT.value,
                data={},
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        await session.commit()

        commands = await (await self._use_case(session)).run_once()
        await session.commit()

        types = [type(c) for c in commands]
        assert CmdCloseGeneralForumTopic in types
        assert CmdEditMessageText in types
        edit = next(c for c in commands if isinstance(c, CmdEditMessageText))
        assert "Время вышло" in edit.text

        # FSM запись удалена
        fsm = await session.get(FsmState, (USER_A, CUSTOMER_CHAT_ID))
        assert fsm is None

    async def test_other_expired_states_are_just_cleaned_no_commands(
        self, session: AsyncSession
    ) -> None:
        await _seed_customer(session)
        # Истёкший FSM в state=help (например, если бы у нас был TTL для help)
        session.add(
            FsmState(
                user_id=USER_A,
                chat_id=CUSTOMER_CHAT_ID,
                state=MenuState.HELP.value,
                data={},
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        await session.commit()
        commands = await (await self._use_case(session)).run_once()
        await session.commit()
        assert commands == []
        fsm = await session.get(FsmState, (USER_A, CUSTOMER_CHAT_ID))
        assert fsm is None

    async def test_no_expired_no_commands(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        # FSM с будущим expires_at
        session.add(
            FsmState(
                user_id=USER_A,
                chat_id=CUSTOMER_CHAT_ID,
                state=MenuState.CREATING_PROMPT.value,
                data={},
                expires_at=datetime.now(UTC) + timedelta(seconds=120),
            )
        )
        await session.commit()
        commands = await (await self._use_case(session)).run_once()
        await session.commit()
        assert commands == []
        # FSM не тронут
        fsm = await session.get(FsmState, (USER_A, CUSTOMER_CHAT_ID))
        assert fsm is not None
