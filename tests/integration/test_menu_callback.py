"""Integration-тесты для use-case HandleMenuCallback (spec 001).

Покрывают Acceptance criteria 001:
- Переключение экранов через CmdEditMessageText (никогда sendMessage).
- FSM-состояние per (user_id, chat_id) хранится в fsm_state с TTL для creating_prompt.
- Idempotency через core_processed_events.
- Список «Мои тикеты» = только тикеты текущего user_id, отсортированные новыми вперёд.
- Пагинация при >10 тикетах.
- При нажатии callback всегда отвечаем cmd.tg.answer_callback_query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from core.domain.menu import MenuAction, MenuState, encode_callback
from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository
from core.repository.models import (
    Customer,
    FsmState,
    ProcessedEvent,
    Ticket,
    TicketStatus,
)
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.handle_menu_callback import (
    HandleMenuCallback,
    MenuCallbackResult,
    Skipped,
)
from shared.events import (
    CmdAnswerCallbackQuery,
    CmdCloseGeneralForumTopic,
    CmdEditMessageText,
    CmdReopenGeneralForumTopic,
    TgCallback,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Test Customer — Тикеты"
USER_A = 11111
USER_B = 22222
MENU_MESSAGE_ID = 7


@pytest.fixture
def use_case(session: AsyncSession) -> HandleMenuCallback:
    return HandleMenuCallback(
        customers=CustomersRepository(session),
        fsm=FsmStateRepository(session),
        tickets=TicketsRepository(session),
        processed=ProcessedEventsRepository(session),
    )


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


async def _seed_tickets(
    session: AsyncSession,
    *,
    customer_id: int,
    user_id: int,
    count: int,
    status: TicketStatus = TicketStatus.NEW,
    starting_topic_id: int = 100,
) -> list[Ticket]:
    """Создать ``count`` тикетов от ``user_id``. ``topic_id`` уникален."""

    tickets = [
        Ticket(
            customer_id=customer_id,
            topic_id=starting_topic_id + i,
            title=f"Тикет {i + 1}",
            description="x",
            status=status,
            created_by_user_id=user_id,
            closed_at=datetime.now(UTC) if status is TicketStatus.CLOSED else None,
        )
        for i in range(count)
    ]
    session.add_all(tickets)
    await session.commit()
    return tickets


def _make_callback(action: MenuAction, *, arg: int | None = None) -> TgCallback:
    return TgCallback(
        event_id=uuid4(),
        chat_id=CUSTOMER_CHAT_ID,
        chat_type="supergroup",
        topic_id=None,  # callback всегда из General — там menu запинено
        user_id=USER_A,
        message_id=MENU_MESSAGE_ID,
        callback_data=encode_callback(action, arg),
        callback_query_id="cb-123",
    )


class TestHappyPath:
    async def test_unknown_callback_namespace_skipped(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = TgCallback(
            event_id=uuid4(),
            chat_id=CUSTOMER_CHAT_ID,
            chat_type="supergroup",
            topic_id=None,
            user_id=USER_A,
            message_id=MENU_MESSAGE_ID,
            callback_data="assign:42:7",  # чужой namespace
            callback_query_id="cb-1",
        )
        result = await use_case.execute(event)
        assert isinstance(result, Skipped)
        assert result.reason == "foreign_namespace"

        # Идемпотентность не должна была сработать — событие в processed_events НЕ записано.
        rows = (await session.execute(select(ProcessedEvent))).scalars().all()
        assert len(rows) == 0

    async def test_main_navigation_to_help_returns_edit_and_answer(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = _make_callback(MenuAction.HELP)

        result = await use_case.execute(event)
        await session.commit()

        assert isinstance(result, MenuCallbackResult)
        assert isinstance(result.edit, CmdEditMessageText)
        assert isinstance(result.answer, CmdAnswerCallbackQuery)
        assert result.edit.chat_id == CUSTOMER_CHAT_ID
        assert result.edit.message_id == MENU_MESSAGE_ID
        # answer всегда без alert при обычном переключении
        assert result.answer.show_alert is False

        # FSM теперь help
        fsm = await FsmStateRepository(session).get(USER_A, CUSTOMER_CHAT_ID)
        assert fsm is not None
        assert fsm.state == MenuState.HELP.value
        # expires_at не выставляется для статичных экранов
        assert fsm.expires_at is None

        # Идемпотентность — event_id записан
        marked = await session.get(ProcessedEvent, event.event_id)
        assert marked is not None

    async def test_main_to_creating_prompt_reopens_general(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = _make_callback(MenuAction.NEW_TICKET)

        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        # Среди extras должен быть reopen General — SPEC §7.2
        assert any(isinstance(cmd, CmdReopenGeneralForumTopic) for cmd in result.extras)
        reopen = next(cmd for cmd in result.extras if isinstance(cmd, CmdReopenGeneralForumTopic))
        assert reopen.chat_id == CUSTOMER_CHAT_ID

    async def test_cancel_from_creating_prompt_closes_general(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        # Сначала перейдём в creating_prompt
        await use_case.execute(_make_callback(MenuAction.NEW_TICKET))
        await session.commit()
        # Теперь отменяем — General должен закрыться
        result = await use_case.execute(_make_callback(MenuAction.CANCEL))
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        assert any(isinstance(cmd, CmdCloseGeneralForumTopic) for cmd in result.extras)

    async def test_main_to_creating_prompt_sets_ttl(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = _make_callback(MenuAction.NEW_TICKET)

        before = datetime.now(UTC)
        result = await use_case.execute(event)
        await session.commit()
        after = datetime.now(UTC)

        assert isinstance(result, MenuCallbackResult)
        fsm = await FsmStateRepository(session).get(USER_A, CUSTOMER_CHAT_ID)
        assert fsm is not None
        assert fsm.state == MenuState.CREATING_PROMPT.value
        assert fsm.expires_at is not None
        # 120 секунд TTL (SPEC §7.2)
        assert before + timedelta(seconds=119) <= fsm.expires_at <= after + timedelta(seconds=121)


class TestIdempotency:
    async def test_same_event_id_skipped_on_second_call(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = _make_callback(MenuAction.HELP)

        first = await use_case.execute(event)
        await session.commit()
        assert isinstance(first, MenuCallbackResult)

        second = await use_case.execute(event)
        await session.commit()
        assert isinstance(second, Skipped)
        assert second.reason == "already_processed"

        # FSM не должен переключаться второй раз — он уже в help, не main.
        fsm = await FsmStateRepository(session).get(USER_A, CUSTOMER_CHAT_ID)
        assert fsm is not None
        assert fsm.state == MenuState.HELP.value


class TestErrors:
    async def test_unknown_customer_returns_alert(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        # Customer'а не создаём
        event = _make_callback(MenuAction.HELP)
        result = await use_case.execute(event)
        assert isinstance(result, Skipped)
        assert result.reason == "unknown_customer"
        assert result.answer is not None
        assert result.answer.show_alert is True

    async def test_invalid_transition_returns_toast(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        # FSM в main, action=BACK — недопустимый переход
        event = _make_callback(MenuAction.BACK)
        result = await use_case.execute(event)
        assert isinstance(result, Skipped)
        assert result.reason == "invalid_transition"
        assert result.answer is not None
        assert result.answer.show_alert is False


class TestMyTickets:
    async def test_lists_only_current_user_active_tickets(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        customer = await _seed_customer(session)
        await _seed_tickets(
            session, customer_id=customer.id, user_id=USER_A, count=3, starting_topic_id=10
        )
        # Чужие тикеты
        await _seed_tickets(
            session, customer_id=customer.id, user_id=USER_B, count=2, starting_topic_id=50
        )
        # Закрытые от USER_A — не должны попадать в «Мои»
        await _seed_tickets(
            session,
            customer_id=customer.id,
            user_id=USER_A,
            count=1,
            status=TicketStatus.CLOSED,
            starting_topic_id=90,
        )

        event = _make_callback(MenuAction.MY_TICKETS)
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        assert result.edit is not None
        assert "(3)" in result.edit.text  # три активных тикета

        # В клавиатуре — 3 строки тикетов + ряд [Закрытые] [Назад]
        keyboard = result.edit.reply_markup["inline_keyboard"]  # type: ignore[index]
        ticket_rows = [r for r in keyboard if len(r) == 1 and "url" in r[0]]
        assert len(ticket_rows) == 3
        # Каждая строка — deep-link на t.me/c/<internal>/<topic_id>
        for row in ticket_rows:
            assert row[0]["url"].startswith("https://t.me/c/1234567890/")

    async def test_pagination_when_more_than_page_size(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        customer = await _seed_customer(session)
        await _seed_tickets(
            session, customer_id=customer.id, user_id=USER_A, count=25, starting_topic_id=1000
        )

        event = _make_callback(MenuAction.MY_TICKETS)
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        assert result.edit is not None
        keyboard = result.edit.reply_markup["inline_keyboard"]  # type: ignore[index]

        # На первой странице — 10 тикетных кнопок (page_size) + ряд пагинации + [Закрытые][Назад]
        url_rows = [r for r in keyboard if len(r) == 1 and "url" in r[0]]
        assert len(url_rows) == 10

        pagination_row = next(
            (
                r
                for r in keyboard
                if any(b.get("callback_data", "").startswith("menu:page:") for b in r)
                or any("/" in b["text"] for b in r if "callback_data" in b)
            ),
            None,
        )
        assert pagination_row is not None
        # На первой странице ◀️ нет, ▶️ есть, индикатор «1/3»
        labels = [b["text"] for b in pagination_row]
        assert "▶️" in labels
        assert "◀️" not in labels
        assert "1/3" in labels

    async def test_empty_my_tickets_shows_placeholder(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        event = _make_callback(MenuAction.MY_TICKETS)
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        assert result.edit is not None
        assert "пока нет" in result.edit.text


class TestBackNavigation:
    async def test_back_from_help_returns_main_screen(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        # сначала переходим в help
        await use_case.execute(_make_callback(MenuAction.HELP))
        await session.commit()

        # потом back → должно быть main
        result = await use_case.execute(_make_callback(MenuAction.BACK))
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        fsm = await FsmStateRepository(session).get(USER_A, CUSTOMER_CHAT_ID)
        assert fsm is not None
        assert fsm.state == MenuState.MAIN.value

    async def test_back_from_closed_tickets_returns_to_my_tickets(
        self, session: AsyncSession, use_case: HandleMenuCallback
    ) -> None:
        await _seed_customer(session)
        # main → my_tickets → closed_tickets → back должен вернуть в my_tickets
        await use_case.execute(_make_callback(MenuAction.MY_TICKETS))
        await session.commit()
        await use_case.execute(_make_callback(MenuAction.CLOSED_TICKETS))
        await session.commit()

        result = await use_case.execute(_make_callback(MenuAction.BACK))
        await session.commit()
        assert isinstance(result, MenuCallbackResult)
        fsm = await session.get(FsmState, (USER_A, CUSTOMER_CHAT_ID))
        assert fsm is not None
        assert fsm.state == MenuState.MY_TICKETS.value
