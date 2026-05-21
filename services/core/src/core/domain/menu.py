"""FSM single-message UI заказчика.

См. SPEC §7.1 — экраны и переходы:

    main ─► creating_prompt   (кнопка «🆕 Новый тикет», реализуется в spec 002)
    main ─► my_tickets        (кнопка «📋 Мои тикеты»)
    main ─► help              (кнопка «❓ Помощь»)
    my_tickets ─► closed_tickets    (кнопка «🗂 Закрытые»)
    closed_tickets ─► my_tickets    (кнопка «⬅️ Назад»)
    my_tickets ─► main              (кнопка «⬅️ Назад»)
    help ─► main                    (кнопка «⬅️ Назад»)
    creating_prompt ─► main         (отмена, таймаут или после создания)

Этот модуль — чистый: никакого IO, никаких таблиц. Только описание состояний,
парсинг callback_data и расчёт целевого состояния. Использование — из
:mod:`core.services.handle_menu_callback`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class MenuState(StrEnum):
    """Состояния single-message UI."""

    MAIN = "main"
    CREATING_PROMPT = "creating_prompt"
    MY_TICKETS = "my_tickets"
    CLOSED_TICKETS = "closed_tickets"
    HELP = "help"


CALLBACK_PREFIX: Final = "menu"
"""Префикс callback_data для всех кнопок меню (SPEC §7.1 + spec 001)."""

# TTL для состояния creating_prompt в секундах (SPEC §7.2: 2 мин).
CREATING_PROMPT_TTL_SECONDS: Final = 120

# Размер страницы для пагинации «Мои тикеты» (SPEC §7.1).
TICKETS_PAGE_SIZE: Final = 10


class MenuAction(StrEnum):
    """Действия, кодируемые в ``callback_data``.

    Формат: ``menu:<action>`` или ``menu:<action>:<arg>``. Не путать с
    ``assign:*``, ``close:*`` — это коллбэки других фич.
    """

    MAIN = "main"
    NEW_TICKET = "new_ticket"
    MY_TICKETS = "my_tickets"
    CLOSED_TICKETS = "closed_tickets"
    HELP = "help"
    BACK = "back"
    CANCEL = "cancel"
    PAGE = "page"
    NOOP = "noop"
    """Кнопка-строка в списке тикетов — обрабатывается как открытие топика через URL-кнопку,
    но если по какой-то причине пришёл callback (старые клиенты), просто отвечаем 'ok'."""


@dataclass(frozen=True, slots=True)
class ParsedCallback:
    """Результат парсинга ``callback_data`` для меню."""

    action: MenuAction
    arg: str | None = None


def parse_callback(data: str) -> ParsedCallback | None:
    """Распарсить ``callback_data`` вида ``menu:<action>[:<arg>]``.

    Возвращает ``None``, если префикс не наш — обработчик пропустит callback
    дальше (например, в ``assign:*`` или ``close:*``).
    """

    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) < 2 or parts[0] != CALLBACK_PREFIX:
        return None
    raw_action = parts[1]
    try:
        action = MenuAction(raw_action)
    except ValueError:
        return None
    arg = parts[2] if len(parts) == 3 else None
    return ParsedCallback(action=action, arg=arg)


def encode_callback(action: MenuAction, arg: str | int | None = None) -> str:
    """Сформировать строку для ``InlineKeyboardButton.callback_data``."""

    if arg is None:
        return f"{CALLBACK_PREFIX}:{action.value}"
    return f"{CALLBACK_PREFIX}:{action.value}:{arg}"


class InvalidTransitionError(ValueError):
    """Поднимается, если переход не определён.

    На уровне handler'а — превращается в toast «Что-то пошло не так», без
    бизнес-эффекта.
    """


def next_state(current: MenuState, action: MenuAction) -> MenuState:
    """Чистый переход состояний.

    Не делает побочных эффектов. Бросает :class:`InvalidTransitionError` для
    действий, не определённых в текущем состоянии.

    Семантика `BACK` зависит от состояния: из ``my_tickets`` и ``help`` идём
    в ``main``; из ``closed_tickets`` идём обратно в ``my_tickets``
    (SPEC §7.1).
    """

    match (current, action):
        # Прямые навигации из main
        case (MenuState.MAIN, MenuAction.NEW_TICKET):
            return MenuState.CREATING_PROMPT
        case (MenuState.MAIN, MenuAction.MY_TICKETS):
            return MenuState.MY_TICKETS
        case (MenuState.MAIN, MenuAction.HELP):
            return MenuState.HELP

        # Углубление: my_tickets → closed_tickets
        case (MenuState.MY_TICKETS, MenuAction.CLOSED_TICKETS):
            return MenuState.CLOSED_TICKETS

        # Возвраты
        case (MenuState.MY_TICKETS, MenuAction.BACK):
            return MenuState.MAIN
        case (MenuState.CLOSED_TICKETS, MenuAction.BACK):
            return MenuState.MY_TICKETS
        case (MenuState.HELP, MenuAction.BACK):
            return MenuState.MAIN

        # Из любого состояния — действие MAIN возвращает в корень.
        case (_, MenuAction.MAIN):
            return MenuState.MAIN

        # Отмена ввода / таймаут (детали — в spec 002)
        case (MenuState.CREATING_PROMPT, MenuAction.CANCEL):
            return MenuState.MAIN

        # Пагинация внутри my_tickets / closed_tickets не меняет состояние.
        case (MenuState.MY_TICKETS, MenuAction.PAGE):
            return MenuState.MY_TICKETS
        case (MenuState.CLOSED_TICKETS, MenuAction.PAGE):
            return MenuState.CLOSED_TICKETS

        case _:
            raise InvalidTransitionError(f"No transition: {current.value} -[{action.value}]-> ?")
