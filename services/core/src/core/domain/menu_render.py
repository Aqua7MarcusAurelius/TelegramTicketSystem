"""Рендеринг экранов single-message UI (SPEC §7.1).

Чистая логика: получает данные на вход (список тикетов, страницу, заголовок
заказчика) и возвращает ``(text, reply_markup)``. Никакого IO, легко тестируется.

Все клавиатуры возвращаются в формате aiogram-совместимого dict — ``reply_markup``
в командах шины (cmd.tg.send_message / cmd.tg.edit_message_text) хранится как
``dict | None`` (см. shared/events/tg.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.domain.menu import (
    TICKETS_PAGE_SIZE,
    MenuAction,
    MenuState,
    encode_callback,
)

# Статус-иконки — синхронизированы со SPEC §6.
STATUS_ICON_NEW = "⚪"
STATUS_ICON_IN_PROGRESS = "🟡"
STATUS_ICON_CLOSED = "✅"


def _supergroup_internal_id(telegram_chat_id: int) -> str:
    """Преобразовать ``-100<digits>`` в ``<digits>`` для deep-link ``t.me/c/``.

    Telegram приватные супергруппы имеют id вида ``-100XXXXXXXXXX``;
    URL-формат бот-канала к топику: ``https://t.me/c/<XXXXXXXXXX>/<msg_id>``.
    """

    raw = str(telegram_chat_id)
    if raw.startswith("-100"):
        return raw[4:]
    # На случай редких форматов (не приватная супергруппа) — берём digits.
    return raw.lstrip("-")


def topic_deep_link(telegram_chat_id: int, topic_id: int) -> str:
    """Deep-link на топик в группе заказчика. SPEC §7.1."""

    return f"https://t.me/c/{_supergroup_internal_id(telegram_chat_id)}/{topic_id}"


@dataclass(frozen=True, slots=True)
class TicketRow:
    """Минимальная проекция ``tickets`` для рендера списков."""

    id: int
    title: str
    status: str  # 'new' | 'in_progress' | 'closed'
    topic_id: int

    @property
    def icon(self) -> str:
        if self.status == "in_progress":
            return STATUS_ICON_IN_PROGRESS
        if self.status == "closed":
            return STATUS_ICON_CLOSED
        return STATUS_ICON_NEW


@dataclass(frozen=True, slots=True)
class Screen:
    """Готовый экран — текст и клавиатура."""

    text: str
    reply_markup: dict[str, Any] | None


# ---------------------------------------------------------------------
# Тексты — выделены в константы для будущей i18n.
# ---------------------------------------------------------------------

MAIN_TEXT = "👋 Здесь вы можете создавать задачи для команды.\n\nВыберите действие:"

CREATING_PROMPT_TEXT = "Опишите задачу одним сообщением 👇"

HELP_TEXT = (
    "ℹ️ <b>Как пользоваться</b>\n"
    "\n"
    "• <b>🆕 Новый тикет</b> — создаёт задачу. Бот спросит описание; "
    "первая строка станет заголовком, остальное — описанием.\n"
    "• <b>📋 Мои тикеты</b> — список активных задач. Кликните на строку, чтобы "
    "перейти в её топик.\n"
    "• <b>🗂 Закрытые</b> — закрытые тикеты за последние 30 дней.\n"
    "\n"
    "Закрыть тикет может только тот, кто его создал. Реоупен невозможен — нужно "
    "новое решение → создаёте новый тикет."
)


def _btn_callback(text: str, action: MenuAction, arg: int | str | None = None) -> dict[str, Any]:
    return {"text": text, "callback_data": encode_callback(action, arg)}


def _btn_url(text: str, url: str) -> dict[str, Any]:
    return {"text": text, "url": url}


def render_main() -> Screen:
    return Screen(
        text=MAIN_TEXT,
        reply_markup={
            "inline_keyboard": [
                [_btn_callback("🆕 Новый тикет", MenuAction.NEW_TICKET)],
                [_btn_callback("📋 Мои тикеты", MenuAction.MY_TICKETS)],
                [_btn_callback("❓ Помощь", MenuAction.HELP)],
            ]
        },
    )


def render_creating_prompt() -> Screen:
    return Screen(
        text=CREATING_PROMPT_TEXT,
        reply_markup={
            "inline_keyboard": [
                [_btn_callback("❌ Отмена", MenuAction.CANCEL)],
            ]
        },
    )


def render_help() -> Screen:
    return Screen(
        text=HELP_TEXT,
        reply_markup={
            "inline_keyboard": [
                [_btn_callback("⬅️ Назад", MenuAction.BACK)],
            ]
        },
    )


def _ticket_rows_keyboard(
    rows: list[TicketRow],
    telegram_chat_id: int,
) -> list[list[dict[str, Any]]]:
    return [
        [
            _btn_url(
                f"{row.icon} #{row.id} {_truncate(row.title, 50)}",
                topic_deep_link(telegram_chat_id, row.topic_id),
            )
        ]
        for row in rows
    ]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _pagination_row(
    *, page: int, total: int, page_size: int = TICKETS_PAGE_SIZE
) -> list[dict[str, Any]] | None:
    """Сформировать ряд с пагинацией, если страниц больше одной.

    ``page`` — 0-based.
    """

    if total <= page_size:
        return None
    pages = (total + page_size - 1) // page_size
    row: list[dict[str, Any]] = []
    if page > 0:
        row.append(_btn_callback("◀️", MenuAction.PAGE, page - 1))
    row.append({"text": f"{page + 1}/{pages}", "callback_data": encode_callback(MenuAction.NOOP)})
    if page < pages - 1:
        row.append(_btn_callback("▶️", MenuAction.PAGE, page + 1))
    return row


def render_my_tickets(
    rows: list[TicketRow],
    *,
    total: int,
    page: int,
    telegram_chat_id: int,
) -> Screen:
    """Экран активных тикетов заказчика (SPEC §7.1)."""

    text = "У вас пока нет активных тикетов." if total == 0 else f"Ваши активные тикеты ({total}):"

    keyboard: list[list[dict[str, Any]]] = _ticket_rows_keyboard(rows, telegram_chat_id)
    pagination = _pagination_row(page=page, total=total)
    if pagination:
        keyboard.append(pagination)
    keyboard.append(
        [
            _btn_callback("🗂 Закрытые", MenuAction.CLOSED_TICKETS),
            _btn_callback("⬅️ Назад", MenuAction.BACK),
        ]
    )
    return Screen(text=text, reply_markup={"inline_keyboard": keyboard})


def render_closed_tickets(
    rows: list[TicketRow],
    *,
    total: int,
    page: int,
    telegram_chat_id: int,
) -> Screen:
    if total == 0:
        text = "За последние 30 дней закрытых тикетов нет."
    else:
        text = f"Закрытые за 30 дней ({total}):"

    keyboard: list[list[dict[str, Any]]] = _ticket_rows_keyboard(rows, telegram_chat_id)
    pagination = _pagination_row(page=page, total=total)
    if pagination:
        keyboard.append(pagination)
    keyboard.append([_btn_callback("⬅️ Назад", MenuAction.BACK)])
    return Screen(text=text, reply_markup={"inline_keyboard": keyboard})


def render_for_state(state: MenuState) -> Screen:
    """Удобный shortcut: рендер экрана без данных (для состояний без списков)."""

    match state:
        case MenuState.MAIN:
            return render_main()
        case MenuState.CREATING_PROMPT:
            return render_creating_prompt()
        case MenuState.HELP:
            return render_help()
        case _:
            raise ValueError(f"State {state.value} requires data — use specific render_* fn")
