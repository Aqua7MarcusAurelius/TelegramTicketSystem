"""aiogram-диспетчер.

Каждый handler делает одно: вызывает соответствующий converter из
:mod:`gateway_tg.inbound.converters` и публикует результат в шину.
Бизнес-логика не здесь — её делает core.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from faststream.redis import RedisBroker
from shared.events.dispatch import stream_for

from gateway_tg.inbound.converters import (
    callback_to_event,
    message_to_event,
    my_chat_member_to_event,
)

log = structlog.get_logger(__name__)


def build_dispatcher(broker: RedisBroker) -> Dispatcher:
    """Собрать aiogram Dispatcher с зарегистрированными подписчиками."""

    dp = Dispatcher()

    @dp.message()
    async def _on_message(message: Message) -> None:
        event = message_to_event(message)
        await broker.publish(event, stream=stream_for(event))

    @dp.edited_message()
    async def _on_edited_message(message: Message) -> None:
        # SPEC v1 не различает edited от обычных; смысловой контракт тот же.
        event = message_to_event(message)
        await broker.publish(event, stream=stream_for(event))

    @dp.callback_query()
    async def _on_callback(callback: CallbackQuery, bot: Bot) -> None:
        event = callback_to_event(callback)
        if event is None:
            # Telegram требует ответа на callback за 3 сек, даже если мы решили
            # ничего не делать — иначе клиент будет крутить спиннер.
            await bot.answer_callback_query(callback.id)
            return
        await broker.publish(event, stream=stream_for(event))

    @dp.my_chat_member()
    async def _on_my_chat_member(update: ChatMemberUpdated) -> None:
        event = my_chat_member_to_event(update)
        await broker.publish(event, stream=stream_for(event))

    log.info("aiogram_dispatcher_built")
    return dp
