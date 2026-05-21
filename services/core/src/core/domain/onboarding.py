"""Тексты и проверки для онбординга группы заказчика. SPEC §3.5, spec 005.

Чистая логика — без IO. Использование — из ``core.services.onboard_customer``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

GENERAL_MENU_NAME: Final = "📋 Меню"
"""Имя, в которое бот переименовывает General группы заказчика."""


@dataclass(frozen=True, slots=True)
class MissingRights:
    can_manage_topics: bool
    can_delete_messages: bool
    can_pin_messages: bool

    @property
    def all_present(self) -> bool:
        return self.can_manage_topics and self.can_delete_messages and self.can_pin_messages

    def missing_labels(self) -> list[str]:
        out: list[str] = []
        if not self.can_manage_topics:
            out.append("Manage Topics")
        if not self.can_delete_messages:
            out.append("Delete Messages")
        if not self.can_pin_messages:
            out.append("Pin Messages")
        return out


def not_a_forum_text() -> str:
    return (
        "⚠️ Эта группа не в режиме форума.\n"
        "\n"
        "Включите: <i>Manage group → Topics: ON</i>, затем нажмите /setup."
    )


def missing_rights_text(missing: MissingRights) -> str:
    labels = missing.missing_labels()
    return (
        "⚠️ Не хватает прав у бота:\n"
        + "\n".join(f"  • {x}" for x in labels)
        + "\n\nДайте права и нажмите кнопку ниже."
    )


def missing_rights_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [[{"text": "🔄 Проверить ещё раз", "callback_data": "setup_recheck"}]]
    }


def already_registered_text(title: str) -> str:
    return f"ℹ️ Эта группа уже подключена как «{title}»."


def success_toast_text(title: str) -> str:
    return f"✅ Группа «{title}» подключена. Заказчик может создавать тикеты."


def setup_recheck_callback_data() -> str:
    return "setup_recheck"
