"""Маппинг ``Event``-классов на имена Redis-стримов.

Единственный источник правды о том, в какой стрим какая команда уходит.
Если добавляется новая ``cmd.tg.*`` команда — её мапинг ДОЛЖЕН быть здесь,
иначе :func:`stream_for` бросит :class:`KeyError`.
"""

from __future__ import annotations

from typing import Final

from shared.events.base import Event
from shared.events.schedule import DailyDigestTick
from shared.events.streams import (
    CMD_TG_ANSWER_CALLBACK_QUERY,
    CMD_TG_CLOSE_FORUM_TOPIC,
    CMD_TG_CLOSE_GENERAL_FORUM_TOPIC,
    CMD_TG_CREATE_FORUM_TOPIC,
    CMD_TG_DELETE_MESSAGE,
    CMD_TG_EDIT_FORUM_TOPIC,
    CMD_TG_EDIT_GENERAL_FORUM_TOPIC,
    CMD_TG_EDIT_MESSAGE_TEXT,
    CMD_TG_PIN_MESSAGE,
    CMD_TG_REOPEN_FORUM_TOPIC,
    CMD_TG_REOPEN_GENERAL_FORUM_TOPIC,
    CMD_TG_SEND_MESSAGE,
    SCHEDULE_DAILY_DIGEST,
    TG_BOT_MEMBERSHIP_CHANGED,
    TG_CALLBACK,
    TG_ERROR,
    TG_MESSAGE,
    TG_MESSAGE_SENT,
    TG_TOPIC_CREATED,
    TICKET_ASSIGNED,
    TICKET_CLOSED,
    TICKET_CREATED,
)
from shared.events.tg import (
    CmdAnswerCallbackQuery,
    CmdCloseForumTopic,
    CmdCloseGeneralForumTopic,
    CmdCreateForumTopic,
    CmdDeleteMessage,
    CmdEditForumTopic,
    CmdEditGeneralForumTopic,
    CmdEditMessageText,
    CmdPinMessage,
    CmdReopenForumTopic,
    CmdReopenGeneralForumTopic,
    CmdSendMessage,
    TgBotMembershipChanged,
    TgCallback,
    TgError,
    TgMessage,
    TgMessageSent,
    TgTopicCreated,
)
from shared.events.ticket import TicketAssigned, TicketClosed, TicketCreated

STREAM_BY_TYPE: Final[dict[type[Event], str]] = {
    # Commands
    CmdSendMessage: CMD_TG_SEND_MESSAGE,
    CmdEditMessageText: CMD_TG_EDIT_MESSAGE_TEXT,
    CmdDeleteMessage: CMD_TG_DELETE_MESSAGE,
    CmdAnswerCallbackQuery: CMD_TG_ANSWER_CALLBACK_QUERY,
    CmdCreateForumTopic: CMD_TG_CREATE_FORUM_TOPIC,
    CmdEditForumTopic: CMD_TG_EDIT_FORUM_TOPIC,
    CmdEditGeneralForumTopic: CMD_TG_EDIT_GENERAL_FORUM_TOPIC,
    CmdCloseForumTopic: CMD_TG_CLOSE_FORUM_TOPIC,
    CmdReopenForumTopic: CMD_TG_REOPEN_FORUM_TOPIC,
    CmdCloseGeneralForumTopic: CMD_TG_CLOSE_GENERAL_FORUM_TOPIC,
    CmdReopenGeneralForumTopic: CMD_TG_REOPEN_GENERAL_FORUM_TOPIC,
    CmdPinMessage: CMD_TG_PIN_MESSAGE,
    # Telegram inbound
    TgMessage: TG_MESSAGE,
    TgCallback: TG_CALLBACK,
    TgTopicCreated: TG_TOPIC_CREATED,
    TgMessageSent: TG_MESSAGE_SENT,
    TgBotMembershipChanged: TG_BOT_MEMBERSHIP_CHANGED,
    TgError: TG_ERROR,
    # Domain
    TicketCreated: TICKET_CREATED,
    TicketAssigned: TICKET_ASSIGNED,
    TicketClosed: TICKET_CLOSED,
    # Schedule
    DailyDigestTick: SCHEDULE_DAILY_DIGEST,
}


def stream_for(event_or_type: Event | type[Event]) -> str:
    """Имя Redis-стрима для публикации события.

    Принимает как экземпляр, так и класс. Бросает ``KeyError`` если тип не
    зарегистрирован — нельзя молча проглотить забытый мапинг.
    """

    t = event_or_type if isinstance(event_or_type, type) else type(event_or_type)
    return STREAM_BY_TYPE[t]
