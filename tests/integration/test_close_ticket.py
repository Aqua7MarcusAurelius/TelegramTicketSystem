"""Integration-тесты для spec 004 — закрытие тикета."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from core.repository.models import Customer, Ticket, TicketEvent, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.close_ticket import (
    CLOSE_CANCEL_PREFIX,
    CLOSE_CONFIRM_PREFIX,
    CLOSE_PREFIX,
    CloseResult,
    CloseSkipped,
    CloseTicket,
    parse_close_callback,
)
from shared.events import (
    CmdAnswerCallbackQuery,
    CmdCloseForumTopic,
    CmdEditForumTopic,
    CmdEditMessageText,
    CmdSendMessage,
    TgCallback,
    TicketClosed,
)
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Test Customer"
CUSTOMER_USER_ID = 5550000  # автор тикета
OTHER_USER_ID = 9999  # кто-то «левый»
TOPIC_ID = 100
HEADER_MESSAGE_ID = 200
ICON_CLOSED = "icon-closed"


async def _seed_customer(session: AsyncSession) -> Customer:
    customer = Customer(
        telegram_chat_id=CUSTOMER_CHAT_ID,
        title=CUSTOMER_TITLE,
        menu_message_id=7,
        onboarded_at=datetime.now(UTC),
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return customer


async def _seed_ticket(
    session: AsyncSession,
    customer_id: int,
    *,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
) -> Ticket:
    ticket = Ticket(
        customer_id=customer_id,
        topic_id=TOPIC_ID,
        header_message_id=HEADER_MESSAGE_ID,
        title="Поправить шапку",
        description="детали",
        status=status,
        created_by_user_id=CUSTOMER_USER_ID,
        in_progress_at=datetime.now(UTC) if status is TicketStatus.IN_PROGRESS else None,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


def _callback(action: str, ticket_id: int, *, from_user_id: int = CUSTOMER_USER_ID) -> TgCallback:
    return TgCallback(
        event_id=uuid4(),
        chat_id=CUSTOMER_CHAT_ID,
        chat_type="supergroup",
        topic_id=TOPIC_ID,
        user_id=from_user_id,
        message_id=HEADER_MESSAGE_ID,
        callback_data=f"{action}:{ticket_id}",
        callback_query_id=f"cb-{action}-{ticket_id}",
    )


@pytest.fixture
def close_use_case(session: AsyncSession) -> CloseTicket:
    return CloseTicket(
        session=session,
        tickets=TicketsRepository(session),
        ticket_events=TicketEventsRepository(session),
        processed=ProcessedEventsRepository(session),
        topic_icon_closed=ICON_CLOSED,
    )


class TestParseCloseCallback:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ("close:42", (CLOSE_PREFIX, 42)),
            ("close_confirm:7", (CLOSE_CONFIRM_PREFIX, 7)),
            ("close_cancel:1", (CLOSE_CANCEL_PREFIX, 1)),
        ],
    )
    def test_valid(self, data: str, expected: tuple[str, int]) -> None:
        assert parse_close_callback(data) == expected

    @pytest.mark.parametrize(
        "data",
        ["assign:1:2", "menu:main", "close:", "close_foo:1", "close:abc", ""],
    )
    def test_invalid(self, data: str) -> None:
        assert parse_close_callback(data) is None


class TestPermission:
    async def test_non_customer_cannot_initiate(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)

        event = _callback(CLOSE_PREFIX, ticket.id, from_user_id=OTHER_USER_ID)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseSkipped)
        assert result.reason == "not_customer"
        assert result.answer.text == "Закрыть тикет может только заказчик"
        # БД не изменилась
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.IN_PROGRESS

    async def test_non_customer_cannot_confirm(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)
        # Левый юзер пытается сразу close_confirm
        event = _callback(CLOSE_CONFIRM_PREFIX, ticket.id, from_user_id=OTHER_USER_ID)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseSkipped)
        assert result.reason == "not_customer"
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.IN_PROGRESS


class TestConfirmDialog:
    async def test_close_button_shows_confirm_dialog(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)

        event = _callback(CLOSE_PREFIX, ticket.id)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseResult)
        # Без побочных событий
        assert result.events == ()
        # Только edit шапки с подтверждением
        assert len(result.commands) == 1
        edit = result.commands[0]
        assert isinstance(edit, CmdEditMessageText)
        assert edit.message_id == HEADER_MESSAGE_ID
        assert "Закрыть тикет?" in edit.text
        kb = edit.reply_markup["inline_keyboard"]  # type: ignore[index]
        labels = [b["text"] for row in kb for b in row]
        assert "Да, закрыть" in labels
        assert "Отмена" in labels
        # Статус всё ещё in_progress
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.IN_PROGRESS

    async def test_cancel_restores_normal_header(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)

        # Откатываем после confirm
        event = _callback(CLOSE_CANCEL_PREFIX, ticket.id)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseResult)
        assert result.events == ()
        edit = result.commands[0]
        assert isinstance(edit, CmdEditMessageText)
        kb = edit.reply_markup["inline_keyboard"]  # type: ignore[index]
        # Только одна кнопка «✅ Закрыть тикет»
        assert len(kb) == 1
        assert kb[0][0]["text"] == "✅ Закрыть тикет"
        # Статус не менялся
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.IN_PROGRESS


class TestCommitClose:
    async def test_full_close_flow(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)

        before = datetime.now(UTC)
        event = _callback(CLOSE_CONFIRM_PREFIX, ticket.id)
        result = await close_use_case.execute(event)
        await session.commit()
        after = datetime.now(UTC)

        assert isinstance(result, CloseResult)
        types = [type(c) for c in result.commands]
        assert types == [
            CmdEditForumTopic,
            CmdSendMessage,
            CmdEditMessageText,
            CmdCloseForumTopic,
        ]

        edit_topic = result.commands[0]
        assert isinstance(edit_topic, CmdEditForumTopic)
        assert edit_topic.name and edit_topic.name.startswith(f"[✅] #{ticket.id}")
        assert edit_topic.icon_custom_emoji_id == ICON_CLOSED

        thanks = result.commands[1]
        assert isinstance(thanks, CmdSendMessage)
        assert thanks.topic_id == TOPIC_ID
        assert "Тикет закрыт" in thanks.text

        final_header = result.commands[2]
        assert isinstance(final_header, CmdEditMessageText)
        assert "✅ Закрыт" in final_header.text
        # Финальная клавиатура без активных кнопок
        assert final_header.reply_markup == {"inline_keyboard": []}

        close_topic = result.commands[3]
        assert isinstance(close_topic, CmdCloseForumTopic)
        assert close_topic.topic_id == TOPIC_ID

        # Событие
        assert len(result.events) == 1
        closed_ev = result.events[0]
        assert isinstance(closed_ev, TicketClosed)
        assert closed_ev.ticket_id == ticket.id
        assert closed_ev.closed_by_user_id == CUSTOMER_USER_ID
        assert before <= closed_ev.closed_at <= after

        # Toast — без alert
        assert isinstance(result.answer, CmdAnswerCallbackQuery)

        # БД
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.CLOSED
        assert ticket.closed_at is not None
        assert ticket.closed_by_user_id == CUSTOMER_USER_ID

        # ticket_events запись 'closed'
        from sqlalchemy import select

        events = (
            (await session.execute(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)))
            .scalars()
            .all()
        )
        assert any(e.event_type == "closed" for e in events)

    async def test_already_closed(self, session: AsyncSession, close_use_case: CloseTicket) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id, status=TicketStatus.CLOSED)
        ticket.closed_at = datetime.now(UTC)
        ticket.closed_by_user_id = CUSTOMER_USER_ID
        await session.commit()

        event = _callback(CLOSE_CONFIRM_PREFIX, ticket.id)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseSkipped)
        assert result.reason == "already_closed"

    async def test_idempotency(self, session: AsyncSession, close_use_case: CloseTicket) -> None:
        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id)

        event = _callback(CLOSE_CONFIRM_PREFIX, ticket.id)
        first = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(first, CloseResult)

        second = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(second, CloseSkipped)
        assert second.reason == "already_processed"

    async def test_close_from_status_new(
        self, session: AsyncSession, close_use_case: CloseTicket
    ) -> None:
        """SPEC §6: переход new → closed напрямую (без in_progress) допустим."""

        customer = await _seed_customer(session)
        ticket = await _seed_ticket(session, customer.id, status=TicketStatus.NEW)

        event = _callback(CLOSE_CONFIRM_PREFIX, ticket.id)
        result = await close_use_case.execute(event)
        await session.commit()
        assert isinstance(result, CloseResult)
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.CLOSED
