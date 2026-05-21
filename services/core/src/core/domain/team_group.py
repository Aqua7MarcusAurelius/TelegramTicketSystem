"""Тексты и константы для онбординга командной группы. SPEC §3.6, spec 006."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class TeamTopicRole(StrEnum):
    """4 топика, которые бот создаёт в командной группе."""

    INCOMING = "incoming"
    DIGEST = "digest"
    LOGS = "logs"
    ESCALATIONS = "escalations"


# Имена форум-топиков. SPEC §3.6.
TOPIC_NAMES: Final[dict[TeamTopicRole, str]] = {
    TeamTopicRole.INCOMING: "🆕 Входящие",
    TeamTopicRole.DIGEST: "📊 Сводка",
    TeamTopicRole.LOGS: "🤖 Логи",
    TeamTopicRole.ESCALATIONS: "🚨 Эскалации",
}

# Порядок создания — фиксированный, важен для предсказуемости env-блока в тестах.
TOPIC_ORDER: Final = (
    TeamTopicRole.INCOMING,
    TeamTopicRole.ESCALATIONS,
    TeamTopicRole.DIGEST,
    TeamTopicRole.LOGS,
)

# Env-имена, в которые попадут получившиеся topic_id. SPEC §13.
ENV_FOR_ROLE: Final[dict[TeamTopicRole, str]] = {
    TeamTopicRole.INCOMING: "EXECUTOR_GROUP_TOPIC_INCOMING",
    TeamTopicRole.DIGEST: "EXECUTOR_GROUP_TOPIC_DIGEST",
    TeamTopicRole.LOGS: "EXECUTOR_GROUP_TOPIC_LOGS",
    TeamTopicRole.ESCALATIONS: None,  # type: ignore[dict-item]  # пока не используется
}


@dataclass(frozen=True, slots=True)
class TeamGroupEnvBlock:
    """Готовый блок env-переменных для копирования в .env."""

    chat_id: int
    incoming: int
    digest: int
    logs: int

    def render(self) -> str:
        return (
            "✅ Командная группа подключена. Скопируйте это в `.env`:\n"
            "\n"
            f"<pre>EXECUTOR_GROUP_CHAT_ID={self.chat_id}\n"
            f"EXECUTOR_GROUP_TOPIC_INCOMING={self.incoming}\n"
            f"EXECUTOR_GROUP_TOPIC_DIGEST={self.digest}\n"
            f"EXECUTOR_GROUP_TOPIC_LOGS={self.logs}</pre>\n"
            "\n"
            "После этого перезапустите сервисы: <code>docker compose restart</code>."
        )


def already_configured_text(existing_chat_id: int) -> str:
    return (
        "⚠️ В .env уже выставлен <code>EXECUTOR_GROUP_CHAT_ID</code> "
        f"({existing_chat_id}) — он не совпадает с этой группой. "
        "Если вы переезжаете на новую командную группу, удалите старые env-переменные "
        "и попробуйте снова."
    )


def not_forum_text() -> str:
    return "⚠️ Эта группа должна быть в режиме форума. Включите Topics: ON и повторите."


def print_topic_id_text(topic_id: int | None) -> str:
    if topic_id is None:
        return "Это General (`message_thread_id` отсутствует у топика-корня)."
    return f"<code>message_thread_id = {topic_id}</code>"
