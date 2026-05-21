"""Use-case: назначить исполнителя на тикет. SPEC §8.2, spec 003.

Вход — :class:`TgCallback` с ``callback_data = "assign:<ticket_id>:<executor_id>"``.

Алгоритм:
1. parse ``callback_data`` → ticket_id, target_executor_id (исполнитель, которого назначаем).
2. Идемпотентность по ``event_id``.
3. Проверка, что нажавший сам — активный исполнитель из ``executors``. Иначе toast.
4. Проверка target_executor — активный и резолвнутый. Иначе toast.
5. Атомарно: ticket.status NEW → IN_PROGRESS, ticket.assignee_id = target.id,
   ticket.in_progress_at = now(). Если ticket уже не NEW — toast «Уже взят: <имя>».
6. INSERT в ticket_events ('assigned', actor=who_clicked).
7. Возврат:
   - команды для group `🆕 Входящие`: edit message — убрать кнопки имён, оставить
     «✅ Взят: <имя>» + URL-кнопку на топик.
   - команды для группы заказчика: edit_forum_topic (icon = IN_PROGRESS),
     edit шапки тикета (статус «🟡 В работе», исполнитель «Команда поддержки»).
   - событие events.ticket.assigned.

В этой спеке: «исполнитель» для заказчика всегда показывается анонимно как
«Команда поддержки» (SPEC §8.2: анонимность перед заказчиком).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from shared.events import (
    CmdAnswerCallbackQuery,
    CmdEditForumTopic,
    CmdEditMessageText,
    Event,
    TgCallback,
    TicketAssigned,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.ticket import header_keyboard, render_header_text
from core.repository.executors import ExecutorsRepository
from core.repository.models import Customer, Executor, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository

ASSIGN_PREFIX = "assign"


def parse_assign_callback(data: str) -> tuple[int, int] | None:
    """``assign:<ticket_id>:<executor_id>`` → (ticket_id, executor_id) или None."""

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != ASSIGN_PREFIX:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class AssignResult:
    commands: tuple[Event, ...]
    events: tuple[Event, ...]
    answer: CmdAnswerCallbackQuery


@dataclass(frozen=True, slots=True)
class AssignSkipped:
    reason: str
    answer: CmdAnswerCallbackQuery


@dataclass(slots=True)
class AssignTicket:
    session: AsyncSession
    tickets: TicketsRepository
    ticket_events: TicketEventsRepository
    executors: ExecutorsRepository
    processed: ProcessedEventsRepository
    topic_icon_in_progress: str | None

    async def execute(self, event: TgCallback) -> AssignResult | AssignSkipped:
        parsed = parse_assign_callback(event.callback_data)
        if parsed is None:
            return AssignSkipped(
                reason="not_assign_callback",
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )
        ticket_id, target_executor_id = parsed

        if not await self.processed.try_mark(event.event_id):
            return AssignSkipped(
                reason="already_processed",
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )

        actor = await self.executors.get_by_telegram_id(event.user_id)
        if actor is None or not actor.is_active:
            return AssignSkipped(
                reason="actor_not_executor",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Вы не в списке исполнителей",
                    show_alert=False,
                ),
            )

        target = await self.executors.get_by_telegram_id(target_executor_id)
        if target is None or not target.is_active or target.telegram_user_id < 0:
            return AssignSkipped(
                reason="target_unresolved",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text=(
                        "Этот исполнитель ещё не доступен — "
                        "попросите его написать в командную группу."
                    ),
                    show_alert=True,
                ),
            )

        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return AssignSkipped(
                reason="ticket_not_found",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Тикет не найден",
                    show_alert=True,
                ),
            )
        if ticket.status is not TicketStatus.NEW or ticket.assignee_id is not None:
            already_label = (
                await self._executor_full_name(ticket.assignee_id)
                if ticket.assignee_id is not None
                else "—"
            )
            return AssignSkipped(
                reason="already_assigned",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text=f"Уже взят: {already_label}",
                    show_alert=False,
                ),
            )

        # Race-safe assignment.
        now = datetime.now(UTC)
        ticket.assignee_id = target.id
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.in_progress_at = now

        await self.ticket_events.record(
            ticket_id=ticket.id,
            event_type="assigned",
            actor_user_id=actor.telegram_user_id,
            payload={"assignee_id": target.id, "assignee_user_id": target.telegram_user_id},
        )

        # Команды в группу заказчика: иконка топика + шапка.
        customer: Customer | None = await self.session.get(Customer, ticket.customer_id)
        commands: list[Event] = []
        if customer is not None and ticket.topic_id is not None:
            commands.append(
                CmdEditForumTopic(
                    chat_id=customer.telegram_chat_id,
                    topic_id=ticket.topic_id,
                    icon_custom_emoji_id=self.topic_icon_in_progress,
                )
            )
            if ticket.header_message_id is not None:
                commands.append(
                    CmdEditMessageText(
                        chat_id=customer.telegram_chat_id,
                        message_id=ticket.header_message_id,
                        text=render_header_text(
                            ticket_id=ticket.id,
                            title=ticket.title,
                            description=ticket.description,
                            status_label="🟡 В работе",
                            assignee_label="Команда поддержки",
                            created_at_human=_format_dt(ticket.created_at),
                        ),
                        reply_markup=header_keyboard(ticket.id),
                        parse_mode="HTML",
                    )
                )

        return AssignResult(
            commands=tuple(commands),
            events=(
                TicketAssigned(
                    ticket_id=ticket.id,
                    assignee_user_id=target.telegram_user_id,
                    assignee_full_name=target.full_name,
                    assigned_by_user_id=actor.telegram_user_id,
                    assigned_at=now,
                ),
            ),
            answer=CmdAnswerCallbackQuery(
                callback_query_id=event.callback_query_id,
                text=f"Назначен: {target.full_name}",
            ),
        )

    async def _executor_full_name(self, executor_id: int) -> str:
        row = await self.session.get(Executor, executor_id)
        return row.full_name if row is not None else "—"


def _format_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d %H:%M")
