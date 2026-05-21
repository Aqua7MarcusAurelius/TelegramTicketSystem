"""Мапперы между «нашим» JSON-форматом и aiogram-объектами.

reply_markup в командах шины хранится как ``dict`` (см. shared/events/tg.py).
aiogram ожидает :class:`InlineKeyboardMarkup`. Чистая функция парсит наш dict
и собирает aiogram-объект.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def to_inline_keyboard(markup: dict[str, Any] | None) -> InlineKeyboardMarkup | None:
    """``{"inline_keyboard": [[{...}, ...], ...]}`` → :class:`InlineKeyboardMarkup`."""

    if markup is None:
        return None
    raw_rows = markup.get("inline_keyboard", [])
    rows: list[list[InlineKeyboardButton]] = []
    for raw_row in raw_rows:
        row: list[InlineKeyboardButton] = []
        for raw_btn in raw_row:
            row.append(
                InlineKeyboardButton(
                    text=raw_btn["text"],
                    callback_data=raw_btn.get("callback_data"),
                    url=raw_btn.get("url"),
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
