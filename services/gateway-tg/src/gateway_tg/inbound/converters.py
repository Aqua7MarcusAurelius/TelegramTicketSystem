"""Конвертеры aiogram-апдейтов в Pydantic-события шины.

Чистые функции, никакого IO. Тестируются крафтом aiogram-объектов напрямую,
без сетевых вызовов.

См. SPEC §9.4 — какие поля и события мы публикуем.
"""

from __future__ import annotations

from typing import Final

from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from shared.events import (
    TgBotMembershipChanged,
    TgCallback,
    TgMessage,
)

# Соответствие aiogram-полей service-message → строковый тип в шине.
_SERVICE_MESSAGE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "forum_topic_created",
    "forum_topic_closed",
    "forum_topic_reopened",
    "forum_topic_edited",
    "general_forum_topic_hidden",
    "general_forum_topic_unhidden",
    "pinned_message",
    "new_chat_members",
    "left_chat_member",
    "new_chat_title",
    "new_chat_photo",
    "delete_chat_photo",
    "group_chat_created",
    "supergroup_chat_created",
    "channel_chat_created",
    "message_auto_delete_timer_changed",
    "migrate_to_chat_id",
    "migrate_from_chat_id",
    "video_chat_started",
    "video_chat_ended",
    "video_chat_participants_invited",
    "video_chat_scheduled",
    "web_app_data",
)


def _detect_service_message(message: Message) -> tuple[bool, str | None]:
    """Понять, является ли сообщение системным, и какой у него тип.

    Возвращает (``is_service_message``, ``service_message_type``).
    """

    for attr in _SERVICE_MESSAGE_ATTRIBUTES:
        if getattr(message, attr, None) is not None:
            return True, attr
    return False, None


def _is_anonymous_admin(message: Message) -> bool:
    """Сообщение от анонимного админа группы.

    Telegram-соглашение (SPEC §11.1): ``message.sender_chat`` совпадает с
    ``message.chat``. Помечает, что отправитель — админ, скрывший identity
    («Send as group»).
    """

    return message.sender_chat is not None and message.sender_chat.id == message.chat.id


def message_to_event(message: Message) -> TgMessage:
    """Преобразовать aiogram :class:`Message` в :class:`TgMessage`."""

    is_service, service_type = _detect_service_message(message)
    user = message.from_user
    user_id = user.id if user is not None else 0
    username = user.username if user is not None else None
    if user is not None:
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
    else:
        full_name = ""
    is_bot = bool(user is not None and user.is_bot)

    return TgMessage(
        chat_id=message.chat.id,
        chat_type=message.chat.type,  # type: ignore[arg-type]
        is_forum=bool(message.chat.is_forum),
        topic_id=message.message_thread_id,
        user_id=user_id,
        username=username,
        full_name=full_name,
        is_anonymous_admin=_is_anonymous_admin(message),
        is_bot=is_bot,
        is_service_message=is_service,
        service_message_type=service_type,
        text=message.text or message.caption,
        message_id=message.message_id,
        reply_to_message_id=(
            message.reply_to_message.message_id if message.reply_to_message is not None else None
        ),
    )


def callback_to_event(callback: CallbackQuery) -> TgCallback | None:
    """Преобразовать aiogram :class:`CallbackQuery` в :class:`TgCallback`.

    Возвращает ``None``, если callback пришёл без `message` или `data` —
    нам нечего обрабатывать. Это не ошибка, просто фильтр.
    """

    if callback.message is None or callback.data is None:
        return None
    return TgCallback(
        chat_id=callback.message.chat.id,
        chat_type=callback.message.chat.type,  # type: ignore[arg-type]
        topic_id=callback.message.message_thread_id,
        user_id=callback.from_user.id,
        message_id=callback.message.message_id,
        callback_data=callback.data,
        callback_query_id=callback.id,
    )


def my_chat_member_to_event(update: ChatMemberUpdated) -> TgBotMembershipChanged:
    """Преобразовать ``my_chat_member`` update в :class:`TgBotMembershipChanged`.

    SPEC §3.5 / §11.1: триггер onboarding для групп заказчиков.
    """

    new = update.new_chat_member
    # Поля прав есть только у ChatMemberAdministrator / Restricted; у других
    # подменим на False (нет прав).
    can_manage_topics = bool(getattr(new, "can_manage_topics", False))
    can_delete_messages = bool(getattr(new, "can_delete_messages", False))
    can_pin_messages = bool(getattr(new, "can_pin_messages", False))

    return TgBotMembershipChanged(
        chat_id=update.chat.id,
        chat_type=update.chat.type,  # type: ignore[arg-type]
        chat_title=update.chat.title,
        is_forum=bool(update.chat.is_forum),
        old_status=update.old_chat_member.status,  # type: ignore[arg-type]
        new_status=new.status,  # type: ignore[arg-type]
        can_manage_topics=can_manage_topics,
        can_delete_messages=can_delete_messages,
        can_pin_messages=can_pin_messages,
        actor_user_id=update.from_user.id,
    )
