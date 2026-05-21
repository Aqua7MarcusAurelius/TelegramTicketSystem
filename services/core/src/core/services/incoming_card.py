"""Use-case'ы для карточки в командной группе → топик `🆕 Входящие`.

- :class:`PublishIncomingCard` — на ``events.ticket.created`` отправляем
  карточку с кнопками-именами исполнителей. correlation_id ставим в команду,
  чтобы потом найти message_id через ``events.tg.message_sent``.
- :class:`AttachIncomingMessageId` — на ``events.tg.message_sent`` с тем же
  correlation_id сохраняем ``tickets.inbox_message_id``.
- :class:`UpdateIncomingAfterAssign` — на ``events.ticket.assigned`` редактируем
  ту же карточку: убираем кнопки, добавляем «✅ Взят: <имя>».
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import structlog
from shared.events import (
    CmdEditMessageText,
    CmdSendMessage,
    Event,
    TgMessageSent,
    TicketAssigned,
    TicketCreated,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.inbox_render import (
    ExecutorButton,
    render_incoming_card,
    render_taken_card,
)
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.models import Executor
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IncomingResult:
    commands: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class IncomingSkipped:
    reason: str


@dataclass(slots=True)
class PublishIncomingCard:
    session: AsyncSession
    tickets: TicketsRepository
    executors: ExecutorsRepository
    customers: CustomersRepository
    processed: ProcessedEventsRepository
    executor_group_chat_id: int | None
    executor_group_topic_incoming: int | None

    async def execute(self, event: TicketCreated) -> IncomingResult | IncomingSkipped:
        if not await self.processed.try_mark(event.event_id):
            return IncomingSkipped(reason="already_processed")

        if self.executor_group_chat_id is None or self.executor_group_topic_incoming is None:
            # SPEC §3.6: командная группа не настроена → не валим, просто скип с warning.
            log.warning("incoming_card_skipped_no_team_group", ticket_id=event.ticket_id)
            return IncomingSkipped(reason="team_group_not_configured")

        ticket = await self.tickets.get(event.ticket_id)
        if ticket is None:
            return IncomingSkipped(reason="ticket_not_found")
        if ticket.topic_id is None:
            # До этой точки фаза 2 уже должна была проставить topic_id —
            # если нет, что-то сломалось в шине.
            return IncomingSkipped(reason="topic_not_attached")

        active_executors = await self.executors.list_active_resolved()
        if not active_executors:
            log.error("incoming_card_no_active_executors", ticket_id=event.ticket_id)

        correlation_id = uuid4()
        await self.tickets.set_inbox_correlation(ticket.id, correlation_id)

        customer = await self.customers.get(event.customer_id)
        if customer is None:
            return IncomingSkipped(reason="customer_missing")

        card = render_incoming_card(
            ticket_id=ticket.id,
            customer_title=customer.title,
            title=ticket.title,
            executors=[
                ExecutorButton(telegram_user_id=e.telegram_user_id, full_name=e.full_name)
                for e in active_executors
            ],
            customer_chat_id=customer.telegram_chat_id,
            topic_id=ticket.topic_id,
        )

        return IncomingResult(
            commands=(
                CmdSendMessage(
                    correlation_id=correlation_id,
                    chat_id=self.executor_group_chat_id,
                    topic_id=self.executor_group_topic_incoming,
                    text=card.text,
                    reply_markup=card.reply_markup,
                    parse_mode="HTML",
                ),
            )
        )


# ---------------------------------------------------------------------
# Phase: events.tg.message_sent для inbox correlation
# ---------------------------------------------------------------------


@dataclass(slots=True)
class AttachIncomingMessageId:
    tickets: TicketsRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessageSent) -> IncomingResult | IncomingSkipped:
        if event.correlation_id is None:
            return IncomingSkipped(reason="no_correlation")
        if not await self.processed.try_mark(event.event_id):
            return IncomingSkipped(reason="already_processed")
        ticket = await self.tickets.get_by_inbox_correlation(event.correlation_id)
        if ticket is None:
            return IncomingSkipped(reason="unknown_inbox_correlation")
        await self.tickets.set_inbox_message(ticket.id, event.message_id)
        return IncomingResult(commands=())


# ---------------------------------------------------------------------
# events.ticket.assigned → правка карточки
# ---------------------------------------------------------------------


@dataclass(slots=True)
class UpdateIncomingAfterAssign:
    session: AsyncSession
    tickets: TicketsRepository
    customers: CustomersRepository
    processed: ProcessedEventsRepository
    executor_group_chat_id: int | None

    async def execute(self, event: TicketAssigned) -> IncomingResult | IncomingSkipped:
        if not await self.processed.try_mark(event.event_id):
            return IncomingSkipped(reason="already_processed")
        if self.executor_group_chat_id is None:
            return IncomingSkipped(reason="team_group_not_configured")

        ticket = await self.tickets.get(event.ticket_id)
        if ticket is None:
            return IncomingSkipped(reason="ticket_not_found")
        if ticket.inbox_message_id is None or ticket.topic_id is None:
            # Карточка ещё не доехала или topic не привязан — пропустим, retry не делаем.
            return IncomingSkipped(reason="inbox_or_topic_missing")

        customer = await self.customers.get(ticket.customer_id)
        if customer is None:
            return IncomingSkipped(reason="customer_missing")

        # Найдём имя исполнителя по telegram_user_id.
        from sqlalchemy import select

        executor = (
            await self.session.execute(
                select(Executor).where(Executor.telegram_user_id == event.assignee_user_id)
            )
        ).scalar_one_or_none()
        assignee_label = executor.full_name if executor else "—"

        card = render_taken_card(
            ticket_id=ticket.id,
            customer_title=customer.title,
            title=ticket.title,
            assignee_full_name=assignee_label,
            customer_chat_id=customer.telegram_chat_id,
            topic_id=ticket.topic_id,
        )
        return IncomingResult(
            commands=(
                CmdEditMessageText(
                    chat_id=self.executor_group_chat_id,
                    message_id=ticket.inbox_message_id,
                    text=card.text,
                    reply_markup=card.reply_markup,
                    parse_mode="HTML",
                ),
            )
        )
