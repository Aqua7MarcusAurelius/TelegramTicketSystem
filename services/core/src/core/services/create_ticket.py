"""Use-case: создание тикета — три фазы.

Создание тикета двухходовое, потому что нужно дождаться ответов Telegram Bot API:
``topic_id`` после ``createForumTopic`` и ``message_id`` шапки после ``sendMessage``.

Фаза 1 — :class:`CreateTicketPhase1`:
    Вход: :class:`TgMessage` от заказчика в General в FSM=creating_prompt.
    Действия:
      - parse_ticket_text → title/description
      - INSERT в tickets (status=new, topic_id=NULL, create_correlation_id=UUID)
      - INSERT в ticket_events ('created')
      - FSM → main
    Команды:
      - cmd.tg.create_forum_topic   (correlation_id = ticket.create_correlation_id)
      - cmd.tg.delete_message       (сообщение заказчика)
      - cmd.tg.close_general_forum_topic
      - cmd.tg.edit_message_text    (меню в main + промежуточный тост)

Фаза 2 — :class:`HandleTopicCreated`:
    Вход: :class:`TgTopicCreated` с correlation_id фазы 1.
    Действия:
      - UPDATE tickets.topic_id = event.topic_id
    Команды:
      - cmd.tg.send_message         (шапка тикета, correlation_id = тот же UUID)

Фаза 3 — :class:`HandleHeaderMessageSent`:
    Вход: :class:`TgMessageSent` с тем же correlation_id.
    Действия:
      - UPDATE tickets.header_message_id = event.message_id
    Команды:
      - cmd.tg.pin_message          (закрепить шапку)
    События:
      - events.ticket.created       (со всем набором полей — listeners уже знают полный путь)

Идемпотентность: каждая фаза перед обработкой делает ``processed.try_mark(event.event_id)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from shared.events import (
    CmdCloseGeneralForumTopic,
    CmdCreateForumTopic,
    CmdDeleteMessage,
    CmdEditMessageText,
    CmdPinMessage,
    CmdSendMessage,
    Event,
    TgMessage,
    TgMessageSent,
    TgTopicCreated,
    TicketCreated,
)

from core.domain.menu import MenuState
from core.domain.menu_render import render_main
from core.domain.ticket import (
    EmptyTicketTextError,
    format_topic_name,
    header_keyboard,
    parse_ticket_text,
    render_header_text,
)
from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository

# ---------------------------------------------------------------------
# Результаты
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TicketResult:
    """Команды + опциональные события, которые handler опубликует в шину."""

    commands: tuple[Event, ...] = ()
    events: tuple[Event, ...] = ()


@dataclass(frozen=True, slots=True)
class TicketSkipped:
    reason: str


# ---------------------------------------------------------------------
# Фаза 1 — создание записи тикета и отправка команд в gateway-tg
# ---------------------------------------------------------------------


# Иконка статуса 'new' для шапки/имени топика. Берётся из env (TOPIC_ICON_NEW),
# но домен это не знает — иконка приходит снаружи через зависимость.


@dataclass(slots=True)
class CreateTicketPhase1:
    customers: CustomersRepository
    fsm: FsmStateRepository
    tickets: TicketsRepository
    ticket_events: TicketEventsRepository
    processed: ProcessedEventsRepository
    topic_icon_new: str | None
    """``custom_emoji_id`` для иконки топика 'new' (env ``TOPIC_ICON_NEW``).

    Может быть None — Telegram примет ``createForumTopic`` без icon (использует дефолт).
    """

    async def execute(self, event: TgMessage) -> TicketResult | TicketSkipped:
        if event.is_service_message or event.is_bot:
            return TicketSkipped(reason="not_user_message")
        if event.text is None or not event.text.strip():
            # Не пытаемся создавать тикет — но handler уровнем выше может почистить сообщение.
            return TicketSkipped(reason="empty_text")

        if not await self.processed.try_mark(event.event_id):
            return TicketSkipped(reason="already_processed")

        customer = await self.customers.get_by_chat(event.chat_id)
        if customer is None:
            return TicketSkipped(reason="unknown_customer")
        if not customer.is_active:
            # SPEC §3.7 / spec 007: деактивированный заказчик не может создавать
            # новые тикеты. Закрывать существующие — может.
            return TicketSkipped(reason="customer_inactive")
        if customer.menu_message_id is None:
            # Меню ещё не создано (onboarding не завершён, spec 005) — не можем
            # редактировать. Лучше явный skip, чем шапку молча уронить.
            return TicketSkipped(reason="menu_not_initialized")

        state = await self.fsm.get_state(user_id=event.user_id, chat_id=event.chat_id)
        if state is not MenuState.CREATING_PROMPT:
            # Заказчик пишет в General вне ожидания. Это не наша зона — обработает
            # отдельный handler (удаление произвольного текста). Возвращаем skip.
            return TicketSkipped(reason="not_creating_prompt")

        try:
            draft = parse_ticket_text(event.text)
        except EmptyTicketTextError:
            return TicketSkipped(reason="empty_after_strip")

        correlation_id = uuid4()
        ticket = await self.tickets.create(
            customer_id=customer.id,
            title=draft.title,
            description=draft.description,
            created_by_user_id=event.user_id,
            create_correlation_id=correlation_id,
        )
        await self.ticket_events.record(
            ticket_id=ticket.id,
            event_type="created",
            actor_user_id=event.user_id,
            payload={"title": draft.title},
        )

        # FSM → main
        await self.fsm.upsert(
            user_id=event.user_id,
            chat_id=event.chat_id,
            state=MenuState.MAIN,
            data={},
            ttl_seconds=None,
        )

        # Команды для Telegram.
        commands: list[Event] = [
            CmdCreateForumTopic(
                correlation_id=correlation_id,
                chat_id=customer.telegram_chat_id,
                name=format_topic_name(ticket.id, ticket.title),
                icon_custom_emoji_id=self.topic_icon_new,
            ),
            CmdDeleteMessage(chat_id=event.chat_id, message_id=event.message_id),
            CmdCloseGeneralForumTopic(chat_id=customer.telegram_chat_id),
            CmdEditMessageText(
                chat_id=customer.telegram_chat_id,
                message_id=customer.menu_message_id,
                text=render_main().text + f"\n\n✅ Тикет #{ticket.id} создан",
                reply_markup=render_main().reply_markup,
                parse_mode="HTML",
            ),
        ]
        return TicketResult(commands=tuple(commands))


# ---------------------------------------------------------------------
# Фаза 2 — пришёл events.tg.topic_created
# ---------------------------------------------------------------------


@dataclass(slots=True)
class HandleTopicCreated:
    tickets: TicketsRepository
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgTopicCreated) -> TicketResult | TicketSkipped:
        if event.correlation_id is None:
            return TicketSkipped(reason="no_correlation")
        if not await self.processed.try_mark(event.event_id):
            return TicketSkipped(reason="already_processed")

        ticket = await self.tickets.get_by_correlation(event.correlation_id)
        if ticket is None:
            # Не наш correlation_id (или ticket удалили) — обработчик других подсистем.
            return TicketSkipped(reason="unknown_correlation")

        await self.tickets.set_topic(ticket.id, event.topic_id)

        customer = await self.customers.get(ticket.customer_id)
        if customer is None:
            return TicketSkipped(reason="customer_missing")

        # Шапка тикета. Будет отправлена с тем же correlation_id — фаза 3 поймёт.
        text = render_header_text(
            ticket_id=ticket.id,
            title=ticket.title,
            description=ticket.description,
            status_label="⚪ Новый",
            assignee_label="не назначен",
            created_at_human=_format_dt(ticket.created_at),
        )
        return TicketResult(
            commands=(
                CmdSendMessage(
                    correlation_id=event.correlation_id,
                    chat_id=customer.telegram_chat_id,
                    topic_id=event.topic_id,
                    text=text,
                    reply_markup=header_keyboard(ticket.id),
                    parse_mode="HTML",
                ),
            )
        )


# ---------------------------------------------------------------------
# Фаза 3 — пришёл events.tg.message_sent (это шапка тикета)
# ---------------------------------------------------------------------


@dataclass(slots=True)
class HandleHeaderMessageSent:
    tickets: TicketsRepository
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessageSent) -> TicketResult | TicketSkipped:
        if event.correlation_id is None:
            return TicketSkipped(reason="no_correlation")
        if not await self.processed.try_mark(event.event_id):
            return TicketSkipped(reason="already_processed")

        ticket = await self.tickets.get_by_correlation(event.correlation_id)
        if ticket is None:
            return TicketSkipped(reason="unknown_correlation")
        if ticket.topic_id is None:
            # Фаза 2 ещё не отработала — события пришли не по порядку. Пока skip,
            # gateway-tg может поставить redelivery. Долгосрочно — нужен дедлайн.
            return TicketSkipped(reason="topic_not_attached_yet")

        await self.tickets.set_header(ticket.id, event.message_id)
        customer = await self.customers.get(ticket.customer_id)
        if customer is None:
            return TicketSkipped(reason="customer_missing")

        return TicketResult(
            commands=(
                CmdPinMessage(
                    chat_id=customer.telegram_chat_id,
                    message_id=event.message_id,
                    disable_notification=True,
                ),
            ),
            events=(
                TicketCreated(
                    ticket_id=ticket.id,
                    customer_id=customer.id,
                    customer_chat_id=customer.telegram_chat_id,
                    customer_title=customer.title,
                    topic_id=ticket.topic_id,
                    title=ticket.title,
                    description=ticket.description,
                    created_by_user_id=ticket.created_by_user_id,
                    created_at=ticket.created_at,
                ),
            ),
        )


def _format_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d %H:%M")
