"""Рендер сообщения в командной группе, топик `🆕 Входящие` (SPEC §8.1).

Чистая логика. Получает данные тикета, имя заказчика и список исполнителей,
возвращает текст + reply_markup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.domain.menu_render import topic_deep_link

BUTTONS_PER_ROW = 3


@dataclass(frozen=True, slots=True)
class ExecutorButton:
    """Минимальная проекция исполнителя для рендера кнопок."""

    telegram_user_id: int
    full_name: str


@dataclass(frozen=True, slots=True)
class IncomingCard:
    text: str
    reply_markup: dict[str, Any]


def render_incoming_card(
    *,
    ticket_id: int,
    customer_title: str,
    title: str,
    executors: list[ExecutorButton],
    customer_chat_id: int,
    topic_id: int,
) -> IncomingCard:
    """SPEC §8.1. Сообщение в командную группу при создании тикета.

    Кнопки имён исполнителей — по 3 в ряд. ``callback_data = "assign:<ticket_id>:<user_id>"``.
    Последний ряд — URL-кнопка с deep-link на топик тикета в группе заказчика.
    """

    text = (
        f"🆕 <b>Новый тикет #{ticket_id}</b>\n"
        f"\n"
        f"Заказчик: <b>{customer_title}</b>\n"
        f"Тема: {title}\n"
        f"\n"
        f"Кто берёт?"
    )

    rows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for ex in executors:
        current.append(
            {
                "text": ex.full_name,
                "callback_data": f"assign:{ticket_id}:{ex.telegram_user_id}",
            }
        )
        if len(current) == BUTTONS_PER_ROW:
            rows.append(current)
            current = []
    if current:
        rows.append(current)

    # URL-кнопка на топик
    rows.append(
        [
            {
                "text": "🔗 Открыть тикет",
                "url": topic_deep_link(customer_chat_id, topic_id),
            }
        ]
    )

    return IncomingCard(text=text, reply_markup={"inline_keyboard": rows})


def render_taken_card(
    *,
    ticket_id: int,
    customer_title: str,
    title: str,
    assignee_full_name: str,
    customer_chat_id: int,
    topic_id: int,
) -> IncomingCard:
    """Редактирование карточки после назначения (SPEC §8.2).

    Кнопки имён убираются, остаётся «✅ Взят: <имя>» в тексте и URL-кнопка.
    """

    text = (
        f"🆕 <b>Тикет #{ticket_id}</b>\n"
        f"\n"
        f"Заказчик: <b>{customer_title}</b>\n"
        f"Тема: {title}\n"
        f"\n"
        f"✅ Взят: <b>{assignee_full_name}</b>"
    )
    return IncomingCard(
        text=text,
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": "🔗 Открыть тикет",
                        "url": topic_deep_link(customer_chat_id, topic_id),
                    }
                ]
            ]
        },
    )
