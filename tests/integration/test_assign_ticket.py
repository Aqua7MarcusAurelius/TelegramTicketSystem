"""Integration-тесты для spec 003 — take ticket (назначение исполнителя).

Покрывают:
- ExecutorsRepository.list_active_resolved / resolve_user_id (загрузка executors.yaml).
- AssignTicket: happy path, idempotency, actor not in executors, already assigned.
- PublishIncomingCard / AttachIncomingMessageId / UpdateIncomingAfterAssign.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.models import Customer, Executor, Ticket, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.assign_ticket import (
    AssignResult,
    AssignSkipped,
    AssignTicket,
)
from core.services.incoming_card import (
    AttachIncomingMessageId,
    IncomingResult,
    IncomingSkipped,
    PublishIncomingCard,
    UpdateIncomingAfterAssign,
)
from core.services.load_executors import (
    ExecutorYaml,
    parse_executors_yaml,
    sync_executors,
)
from shared.events import (
    CmdEditForumTopic,
    CmdEditMessageText,
    CmdSendMessage,
    TgCallback,
    TgMessageSent,
    TicketAssigned,
    TicketCreated,
)
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Test Customer"
TEAM_CHAT_ID = -1009999999999
TEAM_TOPIC_INCOMING = 2

USER_IVAN = 5550001
USER_MARIA = 5550002
USER_PETR = 5550003


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


async def _seed_executors(
    session: AsyncSession,
    *,
    resolve: bool = True,
) -> dict[str, Executor]:
    repo = ExecutorsRepository(session)
    await sync_executors(
        repo,
        [
            ExecutorYaml(username="ivan", full_name="Иван Петров", is_lead=True),
            ExecutorYaml(username="maria", full_name="Мария Сидорова", is_lead=False),
            ExecutorYaml(username="petr", full_name="Пётр Кузнецов", is_lead=False),
        ],
    )
    await session.commit()
    if resolve:
        for username, uid in [
            ("ivan", USER_IVAN),
            ("maria", USER_MARIA),
            ("petr", USER_PETR),
        ]:
            await repo.resolve_user_id(username, uid)
        await session.commit()
    rows = {}
    for u in ["ivan", "maria", "petr"]:
        row = await repo.get_by_username(u)
        assert row is not None
        rows[u] = row
    return rows


async def _seed_ticket(
    session: AsyncSession,
    customer_id: int,
    *,
    status: TicketStatus = TicketStatus.NEW,
    topic_id: int | None = 100,
    header_message_id: int | None = 200,
) -> Ticket:
    ticket = Ticket(
        customer_id=customer_id,
        topic_id=topic_id,
        header_message_id=header_message_id,
        title="Поправить шапку",
        description="детали",
        status=status,
        created_by_user_id=99999,  # заказчик, в spec 003 не важен
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


def _make_assign_callback(*, ticket_id: int, target_user_id: int, from_user_id: int) -> TgCallback:
    return TgCallback(
        event_id=uuid4(),
        chat_id=TEAM_CHAT_ID,
        chat_type="supergroup",
        topic_id=TEAM_TOPIC_INCOMING,
        user_id=from_user_id,
        message_id=42,
        callback_data=f"assign:{ticket_id}:{target_user_id}",
        callback_query_id="cb-x",
    )


@pytest.fixture
def assign_use_case(session: AsyncSession) -> AssignTicket:
    return AssignTicket(
        session=session,
        tickets=TicketsRepository(session),
        ticket_events=TicketEventsRepository(session),
        executors=ExecutorsRepository(session),
        processed=ProcessedEventsRepository(session),
        topic_icon_in_progress="icon-in-progress",
    )


class TestExecutorsLoad:
    async def test_yaml_parser(self) -> None:
        yaml_text = """\
executors:
  - username: ivan
    full_name: Иван
    is_lead: true
  - username: maria
    full_name: Мария
  - username: ""
    full_name: skip
"""
        items = parse_executors_yaml(yaml_text)
        assert len(items) == 2
        assert items[0].username == "ivan"
        assert items[0].is_lead is True
        assert items[1].is_lead is False

    async def test_sync_inserts_and_marks_inactive(self, session: AsyncSession) -> None:
        repo = ExecutorsRepository(session)
        # Первый sync
        await sync_executors(
            repo,
            [
                ExecutorYaml(username="ivan", full_name="Иван", is_lead=True),
                ExecutorYaml(username="maria", full_name="Мария", is_lead=False),
            ],
        )
        await session.commit()
        assert (await repo.get_by_username("ivan")) is not None
        assert (await repo.get_by_username("maria")) is not None

        # Удалили maria из YAML → она должна стать is_active=false
        await sync_executors(
            repo,
            [ExecutorYaml(username="ivan", full_name="Иван", is_lead=True)],
        )
        await session.commit()
        maria = await repo.get_by_username("maria")
        assert maria is not None
        assert maria.is_active is False

    async def test_list_active_resolved_excludes_placeholders(self, session: AsyncSession) -> None:
        repo = ExecutorsRepository(session)
        await sync_executors(
            repo,
            [
                ExecutorYaml(username="ivan", full_name="Иван", is_lead=True),
                ExecutorYaml(username="maria", full_name="Мария", is_lead=False),
            ],
        )
        await session.commit()
        # Никого не резолвили — placeholder'ы < 0
        assert await repo.list_active_resolved() == []

        # Резолвим одного
        await repo.resolve_user_id("ivan", USER_IVAN)
        await session.commit()
        resolved = await repo.list_active_resolved()
        assert len(resolved) == 1
        assert resolved[0].telegram_user_id == USER_IVAN


class TestAssignTicket:
    async def test_happy_path(self, session: AsyncSession, assign_use_case: AssignTicket) -> None:
        customer = await _seed_customer(session)
        execs = await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)

        event = _make_assign_callback(
            ticket_id=ticket.id,
            target_user_id=USER_MARIA,
            from_user_id=USER_IVAN,
        )
        result = await assign_use_case.execute(event)
        await session.commit()

        assert isinstance(result, AssignResult)
        # Имена/иконка
        types = [type(c) for c in result.commands]
        assert CmdEditForumTopic in types
        assert CmdEditMessageText in types
        edit_header = next(c for c in result.commands if isinstance(c, CmdEditMessageText))
        assert "🟡 В работе" in edit_header.text
        assert "Команда поддержки" in edit_header.text
        assert "Мария" not in edit_header.text  # анонимность перед заказчиком

        # Событие
        assert len(result.events) == 1
        assigned = result.events[0]
        assert isinstance(assigned, TicketAssigned)
        assert assigned.assignee_user_id == USER_MARIA
        assert assigned.assigned_by_user_id == USER_IVAN

        # БД
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.IN_PROGRESS
        assert ticket.assignee_id == execs["maria"].id
        assert ticket.in_progress_at is not None

    async def test_self_pickup(self, session: AsyncSession, assign_use_case: AssignTicket) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)

        # Иван назначает себя
        event = _make_assign_callback(
            ticket_id=ticket.id, target_user_id=USER_IVAN, from_user_id=USER_IVAN
        )
        result = await assign_use_case.execute(event)
        await session.commit()
        assert isinstance(result, AssignResult)
        assigned = result.events[0]
        assert isinstance(assigned, TicketAssigned)
        assert assigned.assignee_user_id == assigned.assigned_by_user_id == USER_IVAN

    async def test_actor_not_executor(
        self, session: AsyncSession, assign_use_case: AssignTicket
    ) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)

        # Кликнул левый пользователь
        event = _make_assign_callback(
            ticket_id=ticket.id, target_user_id=USER_MARIA, from_user_id=777
        )
        result = await assign_use_case.execute(event)
        await session.commit()
        assert isinstance(result, AssignSkipped)
        assert result.reason == "actor_not_executor"
        assert result.answer.text == "Вы не в списке исполнителей"
        # Тикет не изменился
        await session.refresh(ticket)
        assert ticket.status is TicketStatus.NEW

    async def test_already_assigned(
        self, session: AsyncSession, assign_use_case: AssignTicket
    ) -> None:
        customer = await _seed_customer(session)
        execs = await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        ticket.assignee_id = execs["petr"].id
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.in_progress_at = datetime.now(UTC)
        await session.commit()

        event = _make_assign_callback(
            ticket_id=ticket.id, target_user_id=USER_MARIA, from_user_id=USER_IVAN
        )
        result = await assign_use_case.execute(event)
        await session.commit()
        assert isinstance(result, AssignSkipped)
        assert result.reason == "already_assigned"
        assert "Пётр" in (result.answer.text or "")

    async def test_idempotency(self, session: AsyncSession, assign_use_case: AssignTicket) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        event = _make_assign_callback(
            ticket_id=ticket.id, target_user_id=USER_MARIA, from_user_id=USER_IVAN
        )
        first = await assign_use_case.execute(event)
        await session.commit()
        assert isinstance(first, AssignResult)

        second = await assign_use_case.execute(event)
        await session.commit()
        assert isinstance(second, AssignSkipped)
        assert second.reason == "already_processed"


class TestIncomingCard:
    async def _publish(self, session: AsyncSession) -> PublishIncomingCard:
        return PublishIncomingCard(
            session=session,
            tickets=TicketsRepository(session),
            executors=ExecutorsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=TEAM_CHAT_ID,
            executor_group_topic_incoming=TEAM_TOPIC_INCOMING,
        )

    async def test_publish_renders_card_with_executor_buttons(self, session: AsyncSession) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        event = TicketCreated(
            ticket_id=ticket.id,
            customer_id=customer.id,
            customer_chat_id=customer.telegram_chat_id,
            topic_id=ticket.topic_id or 0,
            title=ticket.title,
            description=ticket.description,
            created_by_user_id=ticket.created_by_user_id,
            created_at=ticket.created_at,
        )
        result = await (await self._publish(session)).execute(event)
        await session.commit()
        assert isinstance(result, IncomingResult)
        assert len(result.commands) == 1
        send = result.commands[0]
        assert isinstance(send, CmdSendMessage)
        assert send.chat_id == TEAM_CHAT_ID
        assert send.topic_id == TEAM_TOPIC_INCOMING
        keyboard = send.reply_markup["inline_keyboard"]  # type: ignore[index]
        # 3 имени + ряд с URL
        assert any(
            b.get("callback_data", "").startswith("assign:") for row in keyboard for b in row
        )
        # inbox correlation в БД
        await session.refresh(ticket)
        assert ticket.inbox_correlation_id == send.correlation_id

    async def test_no_team_group_skipped(self, session: AsyncSession) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        event = TicketCreated(
            ticket_id=ticket.id,
            customer_id=customer.id,
            customer_chat_id=customer.telegram_chat_id,
            topic_id=ticket.topic_id or 0,
            title=ticket.title,
            description=ticket.description,
            created_by_user_id=ticket.created_by_user_id,
            created_at=ticket.created_at,
        )
        use_case = PublishIncomingCard(
            session=session,
            tickets=TicketsRepository(session),
            executors=ExecutorsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=None,
            executor_group_topic_incoming=None,
        )
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, IncomingSkipped)
        assert result.reason == "team_group_not_configured"


class TestAttachIncomingMessageId:
    async def test_happy_path(self, session: AsyncSession) -> None:
        customer = await _seed_customer(session)
        await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        # Эмулируем PublishIncomingCard
        pub = PublishIncomingCard(
            session=session,
            tickets=TicketsRepository(session),
            executors=ExecutorsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=TEAM_CHAT_ID,
            executor_group_topic_incoming=TEAM_TOPIC_INCOMING,
        )
        event = TicketCreated(
            ticket_id=ticket.id,
            customer_id=customer.id,
            customer_chat_id=customer.telegram_chat_id,
            topic_id=ticket.topic_id or 0,
            title=ticket.title,
            description=ticket.description,
            created_by_user_id=ticket.created_by_user_id,
            created_at=ticket.created_at,
        )
        result_pub = await pub.execute(event)
        await session.commit()
        assert isinstance(result_pub, IncomingResult)
        send = result_pub.commands[0]
        assert isinstance(send, CmdSendMessage)

        # Симулируем events.tg.message_sent
        msg_sent = TgMessageSent(
            correlation_id=send.correlation_id,
            chat_id=TEAM_CHAT_ID,
            topic_id=TEAM_TOPIC_INCOMING,
            message_id=98765,
        )
        attach = AttachIncomingMessageId(
            tickets=TicketsRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await attach.execute(msg_sent)
        await session.commit()
        assert isinstance(result, IncomingResult)
        await session.refresh(ticket)
        assert ticket.inbox_message_id == 98765


class TestUpdateIncomingAfterAssign:
    async def test_edits_card_after_assignment(self, session: AsyncSession) -> None:
        customer = await _seed_customer(session)
        execs = await _seed_executors(session)
        ticket = await _seed_ticket(session, customer.id)
        ticket.assignee_id = execs["maria"].id
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.in_progress_at = datetime.now(UTC)
        ticket.inbox_message_id = 12345
        await session.commit()

        event = TicketAssigned(
            ticket_id=ticket.id,
            assignee_user_id=USER_MARIA,
            assigned_by_user_id=USER_IVAN,
            assigned_at=datetime.now(UTC),
        )
        use_case = UpdateIncomingAfterAssign(
            session=session,
            tickets=TicketsRepository(session),
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=TEAM_CHAT_ID,
        )
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, IncomingResult)
        assert len(result.commands) == 1
        edit = result.commands[0]
        assert isinstance(edit, CmdEditMessageText)
        assert edit.chat_id == TEAM_CHAT_ID
        assert edit.message_id == 12345
        # Имя исполнителя в Backoffice виде (не анонимизируем для команды!)
        assert "Мария" in edit.text
        # Кнопок имён больше нет
        kb = edit.reply_markup["inline_keyboard"]  # type: ignore[index]
        for row in kb:
            for b in row:
                assert not b.get("callback_data", "").startswith("assign:")
