"""Подписчики ``cmd.tg.*`` — вызывают методы aiogram Bot API.

В 8a реализованы 5 базовых команд: send_message, edit_message_text,
delete_message, answer_callback_query, pin_message. Форум-операции
(create_forum_topic и т.п.) и webhook-режим — в 8b.

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
    CmdDeleteMessage,
    CmdEditMessageText,
    CmdPinMessage,
    CmdSendMessage,
    TgMessageSent,
)
from shared.events.dispatch import stream_for
from shared.events.streams import (
    CMD_TG_ANSWER_CALLBACK_QUERY,
    CMD_TG_DELETE_MESSAGE,
    CMD_TG_EDIT_MESSAGE_TEXT,
    CMD_TG_PIN_MESSAGE,
    CMD_TG_SEND_MESSAGE,
)

from gateway_tg.outbound.mappers import to_inline_keyboard

log = structlog.get_logger(__name__)


def register(broker: RedisBroker, bot: Bot) -> None:
    """Зарегистрировать подписчиков на 5 базовых ``cmd.tg.*`` команд."""

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
