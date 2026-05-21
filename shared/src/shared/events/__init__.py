"""Pydantic-схемы шины событий.

Соглашения (см. docs/SPEC.md §9):
- ``events.*`` — факты, ``cmd.*`` — команды.
- Стрим Redis = имя namespace, например ``events.ticket.created``.
- Каждое сообщение наследуется от :class:`Event` и содержит ``event_id``,
  ``event_version``, ``occurred_at``, опционально ``correlation_id``.
"""

from shared.events.base import Event
from shared.events.schedule import DailyDigestTick
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
    TgTopicCreated,
)
from shared.events.ticket import TicketAssigned, TicketClosed, TicketCreated

__all__ = [
    # Base
    "Event",
    # Domain (events.ticket.*)
    "TicketAssigned",
    "TicketClosed",
    "TicketCreated",
    # Telegram inbound (events.tg.*)
    "TgBotMembershipChanged",
    "TgCallback",
    "TgError",
    "TgMessage",
    "TgTopicCreated",
    # Telegram commands (cmd.tg.*)
    "CmdAnswerCallbackQuery",
    "CmdCloseForumTopic",
    "CmdCloseGeneralForumTopic",
    "CmdCreateForumTopic",
    "CmdDeleteMessage",
    "CmdEditForumTopic",
    "CmdEditGeneralForumTopic",
    "CmdEditMessageText",
    "CmdPinMessage",
    "CmdReopenForumTopic",
    "CmdReopenGeneralForumTopic",
    "CmdSendMessage",
    # Schedule
    "DailyDigestTick",
]
