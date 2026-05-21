"""Use-case: закрытие тикета. SPEC §7.4, spec 004.

Обрабатывает три формы ``callback_data`` шапки:
- ``close:<id>`` — заказчик жмёт «✅ Закрыть тикет» → показываем подтверждение
- ``close_cancel:<id>`` — возврат шапки в нормальный вид
- ``close_confirm:<id>`` — финальное закрытие

Закрытие разрешено ТОЛЬКО автору тикета (``tickets.created_by_user_id``). Любой
другой пользователь, нажавший кнопку, получает toast «Закрыть тикет может только
заказчик» и БД не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from shared.events import (
    CmdAnswerCallbackQuery,
    CmdCloseForumTopic,
    CmdEditForumTopic,
    CmdEditMessageText,
    CmdSendMessage,
    Event,
    TgCallback,
    TicketClosed,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.ticket import (
    closed_header_keyboard,
    confirm_close_keyboard,
    format_topic_name,
    header_keyboard,
    render_confirm_close_text,
    render_header_text,
)
from core.repository.models import Customer, Ticket, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository

CLOSE_PREFIX: Final = "close"
CLOSE_CONFIRM_PREFIX: Final = "close_confirm"
CLOSE_CANCEL_PREFIX: Final = "close_cancel"

CloseAction = str  # один из CLOSE_*_PREFIX


def parse_close_callback(data: str) -> tuple[CloseAction, int] | None:
    """``close[_confirm|_cancel]:<ticket_id>`` → (action, ticket_id) или None."""

    parts = data.split(":")
    if len(parts) != 2:
        return None
    action = parts[0]
    if action not in {CLOSE_PREFIX, CLOSE_CONFIRM_PREFIX, CLOSE_CANCEL_PREFIX}:
        return None
    try:
        return action, int(parts[1])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CloseResult:
    commands: tuple[Event, ...]
    events: tuple[Event, ...]
    answer: CmdAnswerCallbackQuery


@dataclass(frozen=True, slots=True)
class CloseSkipped:
    reason: str
    answer: CmdAnswerCallbackQuery


@dataclass(slots=True)
class CloseTicket:
    session: AsyncSession
    tickets: TicketsRepository
    ticket_events: TicketEventsRepository
    processed: ProcessedEventsRepository
    topic_icon_closed: str | None

    async def execute(self, event: TgCallback) -> CloseResult | CloseSkipped:
        parsed = parse_close_callback(event.callback_data)
        if parsed is None:
            return CloseSkipped(
                reason="not_close_callback",
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )
        action, ticket_id = parsed

        if not await self.processed.try_mark(event.event_id):
            return CloseSkipped(
                reason="already_processed",
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )

        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return CloseSkipped(
                reason="ticket_not_found",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Тикет не найден",
                    show_alert=True,
                ),
            )

        if event.user_id != ticket.created_by_user_id:
            # SPEC §7.4 / spec 004 AC: первая проверка — должен быть заказчик
            return CloseSkipped(
                reason="not_customer",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Закрыть тикет может только заказчик",
                    show_alert=False,
                ),
            )

        if ticket.status is TicketStatus.CLOSED:
            return CloseSkipped(
                reason="already_closed",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Тикет уже закрыт",
                ),
            )

        customer: Customer | None = await self.session.get(Customer, ticket.customer_id)
        if customer is None or ticket.topic_id is None or ticket.header_message_id is None:
            return CloseSkipped(
                reason="ticket_not_fully_initialized",
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )

        if action == CLOSE_PREFIX:
            return self._confirm(event, customer, ticket)
        if action == CLOSE_CANCEL_PREFIX:
            return self._cancel(event, customer, ticket)
        return await self._commit_close(event, customer, ticket)

    def _confirm(self, event: TgCallback, customer: Customer, ticket: Ticket) -> CloseResult:
        """``close:<id>`` — заменить кнопку «Закрыть» на пару «Да/Отмена»."""

        base_text = self._header_text(ticket, active=True)
        return CloseResult(
            commands=(
                CmdEditMessageText(
                    chat_id=customer.telegram_chat_id,
                    message_id=ticket.header_message_id,  # type: ignore[arg-type]
                    text=render_confirm_close_text(base_text),
                    reply_markup=confirm_close_keyboard(ticket.id),
                    parse_mode="HTML",
                ),
            ),
            events=(),
            answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
        )

    def _cancel(self, event: TgCallback, customer: Customer, ticket: Ticket) -> CloseResult:
        """``close_cancel:<id>`` — вернуть шапку к нормальному виду."""

        return CloseResult(
            commands=(
                CmdEditMessageText(
                    chat_id=customer.telegram_chat_id,
                    message_id=ticket.header_message_id,  # type: ignore[arg-type]
                    text=self._header_text(ticket, active=True),
                    reply_markup=header_keyboard(ticket.id),
                    parse_mode="HTML",
                ),
            ),
            events=(),
            answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
        )

    async def _commit_close(
        self, event: TgCallback, customer: Customer, ticket: Ticket
    ) -> CloseResult:
        """``close_confirm:<id>`` — атомарно закрываем тикет."""

        now = datetime.now(UTC)
        ticket.status = TicketStatus.CLOSED
        ticket.closed_at = now
        ticket.closed_by_user_id = event.user_id

        await self.ticket_events.record(
            ticket_id=ticket.id,
            event_type="closed",
            actor_user_id=event.user_id,
            payload={},
        )

        # 1) Имя топика + иконка
        commands: list[Event] = [
            CmdEditForumTopic(
                chat_id=customer.telegram_chat_id,
                topic_id=ticket.topic_id,  # type: ignore[arg-type]
                name=format_topic_name(ticket.id, ticket.title, closed=True),
                icon_custom_emoji_id=self.topic_icon_closed,
            ),
            # 2) Финальное сообщение в топик
            CmdSendMessage(
                chat_id=customer.telegram_chat_id,
                topic_id=ticket.topic_id,
                text="✅ Тикет закрыт. Спасибо!",
                parse_mode="HTML",
            ),
            # 3) Шапка — финальный вид без активных кнопок
            CmdEditMessageText(
                chat_id=customer.telegram_chat_id,
                message_id=ticket.header_message_id,  # type: ignore[arg-type]
                text=self._header_text(ticket, active=False),
                reply_markup=closed_header_keyboard(),
                parse_mode="HTML",
            ),
            # 4) Закрытие топика — после всех правок, чтобы они успели уйти
            CmdCloseForumTopic(
                chat_id=customer.telegram_chat_id,
                topic_id=ticket.topic_id,  # type: ignore[arg-type]
            ),
        ]

        return CloseResult(
            commands=tuple(commands),
            events=(
                TicketClosed(
                    ticket_id=ticket.id,
                    closed_by_user_id=event.user_id,
                    closed_at=now,
                ),
            ),
            answer=CmdAnswerCallbackQuery(
                callback_query_id=event.callback_query_id,
                text="Тикет закрыт",
            ),
        )

    def _header_text(self, ticket: Ticket, *, active: bool) -> str:
        """Собрать текст шапки. ``active=False`` — для финального состояния."""

        if not active:
            status_label = "✅ Закрыт"
        elif ticket.status is TicketStatus.IN_PROGRESS:
            status_label = "🟡 В работе"
        else:
            status_label = "⚪ Новый"

        if not active or ticket.assignee_id is None:
            assignee_label = "не назначен" if ticket.assignee_id is None else "Команда поддержки"
        else:
            assignee_label = "Команда поддержки"

        return render_header_text(
            ticket_id=ticket.id,
            title=ticket.title,
            description=ticket.description,
            status_label=status_label,
            assignee_label=assignee_label,
            created_at_human=ticket.created_at.strftime("%Y-%m-%d %H:%M"),
        )
