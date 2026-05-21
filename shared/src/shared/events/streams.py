"""Имена Redis-стримов — единый источник правды для подписчиков и продюсеров.

Имя стрима === namespace события (см. SPEC §9.1).
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------
# Events (facts)
# ---------------------------------------------------------------------

# Domain
TICKET_CREATED: Final = "events.ticket.created"
TICKET_ASSIGNED: Final = "events.ticket.assigned"
TICKET_CLOSED: Final = "events.ticket.closed"

# Telegram inbound
TG_MESSAGE: Final = "events.tg.message"
TG_CALLBACK: Final = "events.tg.callback"
TG_TOPIC_CREATED: Final = "events.tg.topic_created"
TG_BOT_MEMBERSHIP_CHANGED: Final = "events.tg.bot_membership_changed"
TG_ERROR: Final = "events.tg.error"

# Schedule
SCHEDULE_DAILY_DIGEST: Final = "events.schedule.daily_digest"

# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------

CMD_TG_SEND_MESSAGE: Final = "cmd.tg.send_message"
CMD_TG_EDIT_MESSAGE_TEXT: Final = "cmd.tg.edit_message_text"
CMD_TG_DELETE_MESSAGE: Final = "cmd.tg.delete_message"
CMD_TG_ANSWER_CALLBACK_QUERY: Final = "cmd.tg.answer_callback_query"
CMD_TG_CREATE_FORUM_TOPIC: Final = "cmd.tg.create_forum_topic"
CMD_TG_EDIT_FORUM_TOPIC: Final = "cmd.tg.edit_forum_topic"
CMD_TG_EDIT_GENERAL_FORUM_TOPIC: Final = "cmd.tg.edit_general_forum_topic"
CMD_TG_CLOSE_FORUM_TOPIC: Final = "cmd.tg.close_forum_topic"
CMD_TG_REOPEN_FORUM_TOPIC: Final = "cmd.tg.reopen_forum_topic"
CMD_TG_CLOSE_GENERAL_FORUM_TOPIC: Final = "cmd.tg.close_general_forum_topic"
CMD_TG_REOPEN_GENERAL_FORUM_TOPIC: Final = "cmd.tg.reopen_general_forum_topic"
CMD_TG_PIN_MESSAGE: Final = "cmd.tg.pin_message"
