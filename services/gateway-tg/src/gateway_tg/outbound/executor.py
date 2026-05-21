"""Подписчики ``cmd.tg.*`` — вызывают методы aiogram Bot API.

8a добавил 5 базовых команд (send/edit/delete/answer/pin). 8b добавил 7
форум-операций + эмиссию ``events.tg.topic_created`` после успешного
``createForumTopic`` (нужно spec 002 фазе 2 и spec 006).

Идемпотентность по ``event_id`` мы не делаем здесь сознательно: рассинхрон
«я уже отправил, но не успел подтвердить» хуже, чем дубликат сообщения.
Реальная идемпотентность строится на стороне core (через processed_events).
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from faststream.redis import RedisBroker
from shared.events import (
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
    TgMessageSent,
    TgTopicCreated,
)
from shared.events.dispatch import stream_for
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
)

from gateway_tg.outbound.mappers import to_inline_keyboard

log = structlog.get_logger(__name__)


def register(broker: RedisBroker, bot: Bot) -> None:
    """Зарегистрировать подписчиков на 12 ``cmd.tg.*`` команд (8a + 8b)."""

    @broker.subscriber(stream=CMD_TG_SEND_MESSAGE, group="gateway-tg")
    async def _send_message(cmd: CmdSendMessage) -> None:
        try:
            sent = await bot.send_message(
                chat_id=cmd.chat_id,
                message_thread_id=cmd.topic_id,
                text=cmd.text,
                reply_markup=to_inline_keyboard(cmd.reply_markup),
                parse_mode=cmd.parse_mode,
                disable_notification=cmd.disable_notification,
            )
        except TelegramBadRequest as e:
            log.error(
                "send_message_failed",
                chat_id=cmd.chat_id,
                error=str(e),
                correlation_id=cmd.correlation_id,
            )
            return

        # Если у команды был correlation_id — публикуем events.tg.message_sent,
        # чтобы core мог дальше прибить message_id к своей сущности.
        if cmd.correlation_id is not None:
            ack = TgMessageSent(
                correlation_id=cmd.correlation_id,
                chat_id=sent.chat.id,
                topic_id=sent.message_thread_id,
                message_id=sent.message_id,
            )
            await broker.publish(ack, stream=stream_for(ack))

    @broker.subscriber(stream=CMD_TG_EDIT_MESSAGE_TEXT, group="gateway-tg")
    async def _edit_message_text(cmd: CmdEditMessageText) -> None:
        try:
            await bot.edit_message_text(
                chat_id=cmd.chat_id,
                message_id=cmd.message_id,
                text=cmd.text,
                reply_markup=to_inline_keyboard(cmd.reply_markup),
                parse_mode=cmd.parse_mode,
            )
        except TelegramBadRequest as e:
            msg = str(e)
            # «message is not modified» — частый кейс при повторных edits с тем
            # же содержимым; для нас это not-an-error.
            if "message is not modified" in msg.lower():
                log.debug("edit_message_text_unchanged", chat_id=cmd.chat_id)
                return
            log.error("edit_message_text_failed", chat_id=cmd.chat_id, error=msg)

    @broker.subscriber(stream=CMD_TG_DELETE_MESSAGE, group="gateway-tg")
    async def _delete_message(cmd: CmdDeleteMessage) -> None:
        try:
            await bot.delete_message(chat_id=cmd.chat_id, message_id=cmd.message_id)
        except TelegramBadRequest as e:
            # Может быть «message to delete not found» — тоже не ошибка.
            log.debug("delete_message_failed", chat_id=cmd.chat_id, error=str(e))

    @broker.subscriber(stream=CMD_TG_ANSWER_CALLBACK_QUERY, group="gateway-tg")
    async def _answer_callback_query(cmd: CmdAnswerCallbackQuery) -> None:
        try:
            await bot.answer_callback_query(
                callback_query_id=cmd.callback_query_id,
                text=cmd.text,
                show_alert=cmd.show_alert,
            )
        except TelegramBadRequest as e:
            # «query is too old» — нормальная ситуация, если core медленно ответил.
            log.debug("answer_callback_query_failed", error=str(e))

    @broker.subscriber(stream=CMD_TG_PIN_MESSAGE, group="gateway-tg")
    async def _pin_message(cmd: CmdPinMessage) -> None:
        try:
            await bot.pin_chat_message(
                chat_id=cmd.chat_id,
                message_id=cmd.message_id,
                disable_notification=cmd.disable_notification,
            )
        except TelegramBadRequest as e:
            log.error("pin_message_failed", chat_id=cmd.chat_id, error=str(e))

    # -----------------------------------------------------------------
    # Форум-операции (8b). Ответ Bot API на createForumTopic превращается
    # в events.tg.topic_created с тем же correlation_id.
    # -----------------------------------------------------------------

    @broker.subscriber(stream=CMD_TG_CREATE_FORUM_TOPIC, group="gateway-tg")
    async def _create_forum_topic(cmd: CmdCreateForumTopic) -> None:
        try:
            topic = await bot.create_forum_topic(
                chat_id=cmd.chat_id,
                name=cmd.name,
                icon_custom_emoji_id=cmd.icon_custom_emoji_id,
            )
        except TelegramBadRequest as e:
            log.error(
                "create_forum_topic_failed",
                chat_id=cmd.chat_id,
                name=cmd.name,
                error=str(e),
                correlation_id=cmd.correlation_id,
            )
            return

        ack = TgTopicCreated(
            correlation_id=cmd.correlation_id,
            chat_id=cmd.chat_id,
            topic_id=topic.message_thread_id,
            name=topic.name,
        )
        await broker.publish(ack, stream=stream_for(ack))

    @broker.subscriber(stream=CMD_TG_EDIT_FORUM_TOPIC, group="gateway-tg")
    async def _edit_forum_topic(cmd: CmdEditForumTopic) -> None:
        try:
            await bot.edit_forum_topic(
                chat_id=cmd.chat_id,
                message_thread_id=cmd.topic_id,
                name=cmd.name,
                icon_custom_emoji_id=cmd.icon_custom_emoji_id,
            )
        except TelegramBadRequest as e:
            log.error(
                "edit_forum_topic_failed",
                chat_id=cmd.chat_id,
                topic_id=cmd.topic_id,
                error=str(e),
            )

    @broker.subscriber(stream=CMD_TG_CLOSE_FORUM_TOPIC, group="gateway-tg")
    async def _close_forum_topic(cmd: CmdCloseForumTopic) -> None:
        try:
            await bot.close_forum_topic(chat_id=cmd.chat_id, message_thread_id=cmd.topic_id)
        except TelegramBadRequest as e:
            log.error(
                "close_forum_topic_failed",
                chat_id=cmd.chat_id,
                topic_id=cmd.topic_id,
                error=str(e),
            )

    @broker.subscriber(stream=CMD_TG_REOPEN_FORUM_TOPIC, group="gateway-tg")
    async def _reopen_forum_topic(cmd: CmdReopenForumTopic) -> None:
        try:
            await bot.reopen_forum_topic(chat_id=cmd.chat_id, message_thread_id=cmd.topic_id)
        except TelegramBadRequest as e:
            log.error(
                "reopen_forum_topic_failed",
                chat_id=cmd.chat_id,
                topic_id=cmd.topic_id,
                error=str(e),
            )

    @broker.subscriber(stream=CMD_TG_EDIT_GENERAL_FORUM_TOPIC, group="gateway-tg")
    async def _edit_general_forum_topic(cmd: CmdEditGeneralForumTopic) -> None:
        try:
            await bot.edit_general_forum_topic(chat_id=cmd.chat_id, name=cmd.name)
        except TelegramBadRequest as e:
            log.error("edit_general_forum_topic_failed", chat_id=cmd.chat_id, error=str(e))

    @broker.subscriber(stream=CMD_TG_CLOSE_GENERAL_FORUM_TOPIC, group="gateway-tg")
    async def _close_general_forum_topic(cmd: CmdCloseGeneralForumTopic) -> None:
        try:
            await bot.close_general_forum_topic(chat_id=cmd.chat_id)
        except TelegramBadRequest as e:
            # «TOPIC_CLOSED» / «general topic already closed» — не ошибка.
            log.debug("close_general_forum_topic_skipped", chat_id=cmd.chat_id, error=str(e))

    @broker.subscriber(stream=CMD_TG_REOPEN_GENERAL_FORUM_TOPIC, group="gateway-tg")
    async def _reopen_general_forum_topic(cmd: CmdReopenGeneralForumTopic) -> None:
        try:
            await bot.reopen_general_forum_topic(chat_id=cmd.chat_id)
        except TelegramBadRequest as e:
            log.debug("reopen_general_forum_topic_skipped", chat_id=cmd.chat_id, error=str(e))
