"""Парсеры и тексты для admin-команд. SPEC §3.7, spec 007.

Чистая логика, никакого IO. Используется из ``core.services.admin_commands``.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


class AdminCommandParseError(ValueError):
    """Не удалось распарсить admin-команду."""


@dataclass(frozen=True, slots=True)
class RenameArgs:
    chat_id: int
    new_title: str


@dataclass(frozen=True, slots=True)
class ChatIdOnlyArgs:
    chat_id: int


def _split(text: str) -> list[str]:
    """``shlex.split`` поддерживает строки в кавычках (``"My Name"``)."""

    try:
        return shlex.split(text)
    except ValueError as e:
        raise AdminCommandParseError(f"shlex error: {e}") from e


def _to_chat_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as e:
        raise AdminCommandParseError(f"chat_id must be int, got {raw!r}") from e


def parse_rename_customer(text: str) -> RenameArgs:
    parts = _split(text)
    if len(parts) < 3 or parts[0] != "/rename_customer":
        raise AdminCommandParseError('usage: /rename_customer <chat_id> "<new title>"')
    chat_id = _to_chat_id(parts[1])
    new_title = " ".join(parts[2:]).strip()
    if not new_title:
        raise AdminCommandParseError("empty title")
    return RenameArgs(chat_id=chat_id, new_title=new_title)


def parse_chat_id_only(text: str, expected_command: str) -> ChatIdOnlyArgs:
    parts = _split(text)
    if len(parts) != 2 or parts[0] != expected_command:
        raise AdminCommandParseError(f"usage: {expected_command} <chat_id>")
    return ChatIdOnlyArgs(chat_id=_to_chat_id(parts[1]))


def usage_text(command: str) -> str:
    examples = {
        "/rename_customer": '/rename_customer <chat_id> "<новое имя>"',
        "/deactivate_customer": "/deactivate_customer <chat_id>",
        "/activate_customer": "/activate_customer <chat_id>",
    }
    return f"⚠️ Использование: <code>{examples.get(command, command)}</code>"


def customer_not_found_text(chat_id: int) -> str:
    return f"⚠️ Заказчик с chat_id={chat_id} не зарегистрирован."


def rename_success_text(chat_id: int, new_title: str) -> str:
    return f"✅ chat_id={chat_id} переименован: «{new_title}»."


def deactivated_text(chat_id: int) -> str:
    return (
        f"✅ chat_id={chat_id} помечен как неактивный. Новые тикеты не создаются; "
        "существующие можно закрывать."
    )


def activated_text(chat_id: int) -> str:
    return f"✅ chat_id={chat_id} активирован."


def reload_executors_text(processed: int) -> str:
    return f"✅ executors.yaml перечитан: {processed} записей."


def reload_executors_missing_text(path: str) -> str:
    return f"⚠️ Файл {path} не найден."


def list_customers_empty_text() -> str:
    return "Зарегистрированных заказчиков нет."


def list_customers_text(items: list[tuple[int, str, bool]]) -> str:
    """``items`` = [(chat_id, title, is_active), ...]."""

    lines = ["📋 <b>Зарегистрированные заказчики</b>:\n"]
    for chat_id, title, is_active in items:
        mark = "✅" if is_active else "⛔"
        lines.append(f"{mark} <code>{chat_id}</code> — {title}")
    return "\n".join(lines)
