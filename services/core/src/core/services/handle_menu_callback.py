"""Use-case: обработка callback'а с кнопки главного меню.

Spec 001. Алгоритм:

1. Парсим ``callback_data``. Не наш namespace → ничего не делаем.
2. Проверяем идемпотентность по ``event_id``. Уже видели → ничего.
3. Загружаем заказчика по ``chat_id`` (callback пришёл из его группы).
4. Загружаем текущее ``MenuState``.
5. Считаем целевое состояние через :func:`core.domain.menu.next_state`.
6. Сохраняем новое состояние (с TTL для ``creating_prompt``).
7. Рендерим экран:
   - для списков подтягиваем данные из ``TicketsRepository``;
   - для остальных — статичный рендер.
8. Возвращаем :class:`MenuCallbackResult` с двумя командами:
   - ``cmd.tg.edit_message_text`` — обновить меню;
   - ``cmd.tg.answer_callback_query`` — закрыть «крутилку» в Telegram (SPEC §7.1).

Use-case не публикует команды сам — это делает handler-обёртка
(:mod:`core.handlers.tg_callback`). Так use-case остаётся тестируемым без шины.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.events import CmdAnswerCallbackQuery, CmdEditMessageText, TgCallback

from core.domain.menu import (
    CREATING_PROMPT_TTL_SECONDS,
    TICKETS_PAGE_SIZE,
    InvalidTransitionError,
    MenuAction,
    MenuState,
    next_state,
    parse_callback,
)
from core.domain.menu_render import (
    Screen,
    TicketRow,
    render_closed_tickets,
    render_for_state,
    render_my_tickets,
)
from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository
from core.repository.models import Customer, Ticket
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository


@dataclass(frozen=True, slots=True)
class MenuCallbackResult:
    """Команды для публикации в шину после обработки callback'а."""

    edit: CmdEditMessageText | None
    answer: CmdAnswerCallbackQuery
    """Всегда присутствует — Telegram требует ответить на callback за 3 сек (SPEC §7.1)."""

    @property
    def commands(self) -> list[CmdEditMessageText | CmdAnswerCallbackQuery]:
        result: list[CmdEditMessageText | CmdAnswerCallbackQuery] = []
        if self.edit is not None:
            result.append(self.edit)
        result.append(self.answer)
        return result


@dataclass(frozen=True, slots=True)
class Skipped:
    """Use-case ничего не сделал: чужой callback, повтор или неизвестный заказчик."""

    reason: str
    answer: CmdAnswerCallbackQuery | None = None


@dataclass(slots=True)
class HandleMenuCallback:
    """Use-case-обёртка над зависимостями.

    Repositories передаются в конструктор. Метод :meth:`execute` принимает
    :class:`TgCallback` и возвращает результат — :class:`MenuCallbackResult`
    или :class:`Skipped`.
    """

    customers: CustomersRepository
    fsm: FsmStateRepository
    tickets: TicketsRepository
    processed: ProcessedEventsRepository
    _deps_initialised: bool = field(default=True, init=False, repr=False)

    async def execute(self, event: TgCallback) -> MenuCallbackResult | Skipped:
        parsed = parse_callback(event.callback_data)
        if parsed is None:
            # Не наш callback — отдадим управление другим обработчикам.
            return Skipped(reason="foreign_namespace")

        if not await self.processed.try_mark(event.event_id):
            return Skipped(reason="already_processed")

        customer = await self.customers.get_by_chat(event.chat_id)
        if customer is None:
            return Skipped(
                reason="unknown_customer",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Группа не зарегистрирована. Обратитесь к команде.",
                    show_alert=True,
                ),
            )

        current_state = await self.fsm.get_state(user_id=event.user_id, chat_id=event.chat_id)

        # PAGE — частный случай: состояние не меняется, action == PAGE.
        try:
            target_state = next_state(current_state, parsed.action)
        except InvalidTransitionError:
            return Skipped(
                reason="invalid_transition",
                answer=CmdAnswerCallbackQuery(
                    callback_query_id=event.callback_query_id,
                    text="Действие сейчас недоступно",
                ),
            )

        if parsed.action is MenuAction.NOOP:
            # Кнопка-индикатор пагинации «1/3» — просто закрываем спиннер.
            return MenuCallbackResult(
                edit=None,
                answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
            )

        # Сохраняем новое состояние. Для CREATING_PROMPT — ставим TTL.
        await self.fsm.upsert(
            user_id=event.user_id,
            chat_id=event.chat_id,
            state=target_state,
            data=_state_data(parsed),
            ttl_seconds=(
                CREATING_PROMPT_TTL_SECONDS if target_state is MenuState.CREATING_PROMPT else None
            ),
        )

        screen = await self._render(
            state=target_state,
            page=_page_from(parsed),
            customer=customer,
            user_id=event.user_id,
        )

        return MenuCallbackResult(
            edit=CmdEditMessageText(
                chat_id=event.chat_id,
                message_id=event.message_id,
                text=screen.text,
                reply_markup=screen.reply_markup,
                parse_mode="HTML",
            ),
            answer=CmdAnswerCallbackQuery(callback_query_id=event.callback_query_id),
        )

    async def _render(
        self,
        *,
        state: MenuState,
        page: int,
        customer: Customer,
        user_id: int,
    ) -> Screen:
        if state is MenuState.MY_TICKETS:
            total = await self.tickets.count_active_by_user(
                customer_id=customer.id, created_by_user_id=user_id
            )
            rows = await self.tickets.list_active_by_user(
                customer_id=customer.id,
                created_by_user_id=user_id,
                offset=page * TICKETS_PAGE_SIZE,
                limit=TICKETS_PAGE_SIZE,
            )
            return render_my_tickets(
                [_to_row(t) for t in rows],
                total=total,
                page=page,
                telegram_chat_id=customer.telegram_chat_id,
            )

        if state is MenuState.CLOSED_TICKETS:
            rows = await self.tickets.list_closed_by_user(
                customer_id=customer.id,
                created_by_user_id=user_id,
                offset=page * TICKETS_PAGE_SIZE,
                limit=TICKETS_PAGE_SIZE,
            )
            # Для total закрытых — в v1 хватает len(rows) на странице (точный
            # счётчик добавим вместе с фильтром «за 30 дней» в spec 002+).
            total = len(rows) + page * TICKETS_PAGE_SIZE if rows else 0
            return render_closed_tickets(
                [_to_row(t) for t in rows],
                total=total,
                page=page,
                telegram_chat_id=customer.telegram_chat_id,
            )

        return render_for_state(state)


def _state_data(parsed: object) -> dict[str, int]:
    """Сохраняем текущую страницу в FSM ``data`` — чтобы при возврате
    из ``closed_tickets`` помнить, на какой странице ``my_tickets`` мы были.
    """

    from core.domain.menu import ParsedCallback  # avoid cycle in linter

    if not isinstance(parsed, ParsedCallback):
        return {}
    if parsed.action is MenuAction.PAGE and parsed.arg is not None:
        try:
            return {"page": int(parsed.arg)}
        except ValueError:
            return {}
    return {}


def _page_from(parsed: object) -> int:
    from core.domain.menu import ParsedCallback

    if not isinstance(parsed, ParsedCallback):
        return 0
    if parsed.action is MenuAction.PAGE and parsed.arg is not None:
        try:
            return max(0, int(parsed.arg))
        except ValueError:
            return 0
    return 0


def _to_row(t: Ticket) -> TicketRow:
    return TicketRow(id=t.id, title=t.title, status=t.status.value, topic_id=t.topic_id)
