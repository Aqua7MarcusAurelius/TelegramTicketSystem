"""Unit-тесты конвертеров aiogram-апдейтов → events.tg.* (gateway-tg, 8a).

Крафтим aiogram-объекты напрямую — без сетевых вызовов, без mock-сессии.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    ForumTopicClosed,
    GeneralForumTopicHidden,
    Message,
    User,
)
from gateway_tg.inbound.converters import (
    callback_to_event,
    message_to_event,
    my_chat_member_to_event,
)

CHAT_ID = -1001234567890
USER_ID = 5550001


def _chat(*, is_forum: bool = True, title: str = "X") -> Chat:
    return Chat(id=CHAT_ID, type="supergroup", is_forum=is_forum, title=title)


def _user(*, is_bot: bool = False, username: str | None = "ivan") -> User:
    return User(id=USER_ID, is_bot=is_bot, first_name="Иван", last_name="Петров", username=username)


def _msg(**overrides) -> Message:  # type: ignore[no-untyped-def]
    base = {
        "message_id": 42,
        "date": datetime.now(UTC),
        "chat": _chat(),
        "from_user": _user(),
        "text": "hello",
    }
    base.update(overrides)
    return Message(**base)


class TestMessageToEvent:
    def test_basic_user_message(self) -> None:
        ev = message_to_event(_msg())
        assert ev.chat_id == CHAT_ID
        assert ev.chat_type == "supergroup"
        assert ev.is_forum is True
        assert ev.user_id == USER_ID
        assert ev.username == "ivan"
        assert ev.full_name == "Иван Петров"
        assert ev.is_bot is False
        assert ev.is_anonymous_admin is False
        assert ev.is_service_message is False
        assert ev.service_message_type is None
        assert ev.text == "hello"
        assert ev.message_id == 42

    def test_topic_message_carries_thread_id(self) -> None:
        ev = message_to_event(_msg(message_thread_id=7))
        assert ev.topic_id == 7

    def test_anonymous_admin_detected(self) -> None:
        chat = _chat()
        # sender_chat = chat → анонимный админ
        ev = message_to_event(_msg(chat=chat, sender_chat=chat))
        assert ev.is_anonymous_admin is True

    def test_service_forum_topic_closed(self) -> None:
        ev = message_to_event(_msg(text=None, forum_topic_closed=ForumTopicClosed()))
        assert ev.is_service_message is True
        assert ev.service_message_type == "forum_topic_closed"

    def test_service_general_forum_topic_hidden(self) -> None:
        ev = message_to_event(_msg(text=None, general_forum_topic_hidden=GeneralForumTopicHidden()))
        assert ev.is_service_message is True
        assert ev.service_message_type == "general_forum_topic_hidden"

    def test_caption_fallback_when_text_none(self) -> None:
        ev = message_to_event(_msg(text=None, caption="caption text"))
        assert ev.text == "caption text"

    def test_bot_user(self) -> None:
        ev = message_to_event(_msg(from_user=_user(is_bot=True)))
        assert ev.is_bot is True

    def test_reply_to_message_id_extracted(self) -> None:
        reply = _msg(message_id=100, text="reply target")
        ev = message_to_event(_msg(reply_to_message=reply))
        assert ev.reply_to_message_id == 100


class TestCallbackToEvent:
    def test_basic(self) -> None:
        cb = CallbackQuery(
            id="cb-1",
            from_user=_user(),
            chat_instance="chat-instance",
            message=_msg(message_id=7),
            data="menu:my_tickets",
        )
        ev = callback_to_event(cb)
        assert ev is not None
        assert ev.callback_data == "menu:my_tickets"
        assert ev.callback_query_id == "cb-1"
        assert ev.message_id == 7
        assert ev.user_id == USER_ID
        assert ev.chat_id == CHAT_ID

    def test_without_message_returns_none(self) -> None:
        cb = CallbackQuery(
            id="cb-2",
            from_user=_user(),
            chat_instance="chat-instance",
            data="menu:main",
        )
        assert callback_to_event(cb) is None

    def test_without_data_returns_none(self) -> None:
        cb = CallbackQuery(
            id="cb-3",
            from_user=_user(),
            chat_instance="chat-instance",
            message=_msg(),
        )
        assert callback_to_event(cb) is None


class TestMyChatMemberToEvent:
    def test_promoted_to_administrator(self) -> None:
        admin = ChatMemberAdministrator(
            user=_user(is_bot=True),
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
            can_post_messages=False,
            can_edit_messages=False,
            can_pin_messages=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_manage_topics=True,
        )
        old = ChatMemberLeft(user=_user(is_bot=True))
        update = ChatMemberUpdated(
            chat=_chat(),
            from_user=_user(),
            date=datetime.now(UTC),
            old_chat_member=old,
            new_chat_member=admin,
        )
        ev = my_chat_member_to_event(update)
        assert ev.new_status == "administrator"
        assert ev.old_status == "left"
        assert ev.can_manage_topics is True
        assert ev.can_delete_messages is True
        assert ev.can_pin_messages is True
        assert ev.is_forum is True
        assert ev.actor_user_id == USER_ID

    def test_demoted_to_member(self) -> None:
        old = ChatMemberAdministrator(
            user=_user(is_bot=True),
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
            can_post_messages=False,
            can_edit_messages=False,
            can_pin_messages=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_manage_topics=True,
        )
        new = ChatMemberMember(user=_user(is_bot=True))
        update = ChatMemberUpdated(
            chat=_chat(),
            from_user=_user(),
            date=datetime.now(UTC),
            old_chat_member=old,
            new_chat_member=new,
        )
        ev = my_chat_member_to_event(update)
        assert ev.new_status == "member"
        assert ev.can_manage_topics is False  # у member-а нет admin-прав
        assert ev.can_delete_messages is False
        assert ev.can_pin_messages is False


@pytest.mark.parametrize(
    "attr, expected_type",
    [
        ("forum_topic_closed", "forum_topic_closed"),
        ("general_forum_topic_hidden", "general_forum_topic_hidden"),
    ],
)
def test_service_message_types_table(attr: str, expected_type: str) -> None:
    klass = {
        "forum_topic_closed": ForumTopicClosed,
        "general_forum_topic_hidden": GeneralForumTopicHidden,
    }[attr]
    ev = message_to_event(_msg(text=None, **{attr: klass()}))
    assert ev.is_service_message is True
    assert ev.service_message_type == expected_type
