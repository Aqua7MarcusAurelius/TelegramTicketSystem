"""События и команды для общения с Telegram (``events.tg.*`` / ``cmd.tg.*``).

См. docs/SPEC.md §9.4 (входящие апдейты) и §9.5 (команды к gateway-tg).
"""

from __future__ import annotations

from typing import Any, Literal

from shared.events.base import Event

ChatType = Literal["private", "group", "supergroup", "channel"]
MembershipStatus = Literal[
    "creator", "administrator", "member", "restricted", "left", "kicked"
]
ParseMode = Literal["HTML", "MarkdownV2"]


# ---------------------------------------------------------------------
# Входящие события от gateway-tg
# ---------------------------------------------------------------------


class TgMessage(Event):
    """``events.tg.message`` — сообщение в любом чате, где есть бот."""

    chat_id: int
    chat_type: ChatType
    is_forum: bool
    topic_id: int | None
    user_id: int
    username: str | None
    full_name: str
    is_anonymous_admin: bool
    """Сообщение от анонимного админа группы (исполнитель в группе заказчика)."""
    is_bot: bool
    is_service_message: bool
    """Системное сообщение (forum_topic_closed/reopened/created/edited и т.п.)."""
    service_message_type: str | None
    text: str | None
    message_id: int
    reply_to_message_id: int | None


class TgCallback(Event):
    """``events.tg.callback`` — нажатие inline-кнопки."""

    chat_id: int
    chat_type: ChatType
    topic_id: int | None
    user_id: int
    message_id: int
    callback_data: str
    callback_query_id: str


class TgTopicCreated(Event):
    """``events.tg.topic_created`` — ответ на ``cmd.tg.create_forum_topic``.

    Связывается с командой через ``correlation_id``.
    """

    chat_id: int
    topic_id: int
    name: str


class TgBotMembershipChanged(Event):
    """``events.tg.bot_membership_changed`` — Telegram ``my_chat_member`` update.

    Триггер onboarding-флоу для групп заказчиков (см. SPEC §3.5).
    """

    chat_id: int
    chat_type: ChatType
    chat_title: str | None
    is_forum: bool
    old_status: MembershipStatus
    new_status: MembershipStatus
    can_manage_topics: bool
    can_delete_messages: bool
    can_pin_messages: bool
    actor_user_id: int


class TgError(Event):
    """``events.tg.error`` — gateway-tg не смог выполнить команду.

    Опциональное событие для observability. ``correlation_id`` указывает на упавшую команду.
    """

    method: str
    """Метод Bot API, который упал (например, ``createForumTopic``)."""
    error_code: int | None
    description: str


# ---------------------------------------------------------------------
# Команды к gateway-tg
# ---------------------------------------------------------------------


class CmdSendMessage(Event):
    """``cmd.tg.send_message``."""

    chat_id: int
    topic_id: int | None = None
    text: str
    reply_markup: dict[str, Any] | None = None
    parse_mode: ParseMode | None = "HTML"
    disable_notification: bool = False


class CmdEditMessageText(Event):
    """``cmd.tg.edit_message_text``."""

    chat_id: int
    message_id: int
    text: str
    reply_markup: dict[str, Any] | None = None
    parse_mode: ParseMode | None = "HTML"


class CmdDeleteMessage(Event):
    """``cmd.tg.delete_message``."""

    chat_id: int
    message_id: int


class CmdAnswerCallbackQuery(Event):
    """``cmd.tg.answer_callback_query``.

    Должен быть отправлен в течение 3 секунд после получения ``TgCallback``.
    """

    callback_query_id: str
    text: str | None = None
    show_alert: bool = False


class CmdCreateForumTopic(Event):
    """``cmd.tg.create_forum_topic``.

    Ответ — ``events.tg.topic_created`` с тем же ``correlation_id``.
    """

    chat_id: int
    name: str
    icon_custom_emoji_id: str | None = None


class CmdEditForumTopic(Event):
    """``cmd.tg.edit_forum_topic`` — изменить имя и/или иконку существующего топика."""

    chat_id: int
    topic_id: int
    name: str | None = None
    icon_custom_emoji_id: str | None = None


class CmdEditGeneralForumTopic(Event):
    """``cmd.tg.edit_general_forum_topic``.

    Используется для переименования General в «📋 Меню» при onboarding'е (SPEC §3.5).
    """

    chat_id: int
    name: str


class CmdCloseForumTopic(Event):
    """``cmd.tg.close_forum_topic``."""

    chat_id: int
    topic_id: int


class CmdReopenForumTopic(Event):
    """``cmd.tg.reopen_forum_topic``."""

    chat_id: int
    topic_id: int


class CmdCloseGeneralForumTopic(Event):
    """``cmd.tg.close_general_forum_topic``."""

    chat_id: int


class CmdReopenGeneralForumTopic(Event):
    """``cmd.tg.reopen_general_forum_topic``."""

    chat_id: int


class CmdPinMessage(Event):
    """``cmd.tg.pin_message``."""

    chat_id: int
    message_id: int
    disable_notification: bool = True
