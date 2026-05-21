"""Unit-тесты executor'а cmd.tg.* (gateway-tg, 8a).

aiogram.Bot мокается через AsyncMock — мы не делаем сетевых вызовов.
FastStream RedisBroker используется в режиме «TestBroker» для inproc-публикации;
здесь нам достаточно мокнуть `broker.publish` для проверки эмиссии TgMessageSent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, User
from gateway_tg.outbound.executor import register
from gateway_tg.outbound.mappers import to_inline_keyboard
from shared.events import (
    CmdAnswerCallbackQuery,
    CmdDeleteMessage,
    CmdEditMessageText,
    CmdPinMessage,
    CmdSendMessage,
    TgMessageSent,
)

# ---------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------


class TestToInlineKeyboard:
    def test_none(self) -> None:
        assert to_inline_keyboard(None) is None

    def test_callback_button(self) -> None:
        kb = to_inline_keyboard(
            {"inline_keyboard": [[{"text": "Hi", "callback_data": "menu:main"}]]}
        )
        assert kb is not None
        assert kb.inline_keyboard[0][0].text == "Hi"
        assert kb.inline_keyboard[0][0].callback_data == "menu:main"
        assert kb.inline_keyboard[0][0].url is None

    def test_url_button(self) -> None:
        kb = to_inline_keyboard(
            {"inline_keyboard": [[{"text": "Open", "url": "https://t.me/c/1/2"}]]}
        )
        assert kb is not None
        assert kb.inline_keyboard[0][0].url == "https://t.me/c/1/2"

    def test_multiple_rows(self) -> None:
        kb = to_inline_keyboard(
            {
                "inline_keyboard": [
                    [
                        {"text": "A", "callback_data": "a"},
                        {"text": "B", "callback_data": "b"},
                    ],
                    [{"text": "C", "callback_data": "c"}],
                ]
            }
        )
        assert kb is not None
        assert len(kb.inline_keyboard) == 2
        assert len(kb.inline_keyboard[0]) == 2


# ---------------------------------------------------------------------
# Executor helpers
# ---------------------------------------------------------------------


def _make_subscribed_broker_and_bot() -> tuple[MagicMock, AsyncMock, dict[str, AsyncMock]]:
    """Имитируем FastStream RedisBroker и aiogram Bot.

    Возвращает (broker, bot, handlers_by_stream). После ``register(broker, bot)``
    в handlers_by_stream лежат сами async-функции — их можно вызывать руками.
    """

    handlers: dict[str, AsyncMock] = {}

    def subscriber(*, stream: str, group: str):
        def deco(fn):
            handlers[stream] = fn
            return fn

        return deco

    broker = MagicMock()
    broker.subscriber = subscriber
    broker.publish = AsyncMock()
    bot = AsyncMock()
    register(broker, bot)
    return broker, bot, handlers


def _fake_sent_message(*, message_id: int = 555) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=-1001234567890, type="supergroup", is_forum=True, title="X"),
        from_user=User(id=1, is_bot=True, first_name="Bot"),
        text="ok",
    )


def _bad_request(text: str = "boom") -> TelegramBadRequest:
    """Соберём TelegramBadRequest, не лезя в aiogram-specific конструктор."""

    method = MagicMock(spec=TelegramMethod)
    return TelegramBadRequest(method=method, message=text)


# ---------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------


class TestSendMessage:
    async def test_calls_bot_with_mapped_args(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        bot.send_message.return_value = _fake_sent_message(message_id=10)

        cmd = CmdSendMessage(
            chat_id=-1001234567890,
            topic_id=7,
            text="hello",
            reply_markup={"inline_keyboard": [[{"text": "X", "callback_data": "y"}]]},
            parse_mode="HTML",
            disable_notification=True,
        )
        await handlers["cmd.tg.send_message"](cmd)

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["chat_id"] == -1001234567890
        assert kwargs["message_thread_id"] == 7
        assert kwargs["text"] == "hello"
        assert kwargs["parse_mode"] == "HTML"
        assert kwargs["disable_notification"] is True
        # reply_markup собрался в InlineKeyboardMarkup
        assert kwargs["reply_markup"] is not None

    async def test_emits_message_sent_when_correlation_present(self) -> None:
        broker, bot, handlers = _make_subscribed_broker_and_bot()
        sent = _fake_sent_message(message_id=12345)
        bot.send_message.return_value = sent

        corr = uuid4()
        cmd = CmdSendMessage(
            correlation_id=corr,
            chat_id=-1001234567890,
            text="hi",
        )
        await handlers["cmd.tg.send_message"](cmd)

        broker.publish.assert_awaited_once()
        args = broker.publish.await_args.args
        published_event = args[0]
        assert isinstance(published_event, TgMessageSent)
        assert published_event.correlation_id == corr
        assert published_event.message_id == 12345
        assert broker.publish.await_args.kwargs["stream"] == "events.tg.message_sent"

    async def test_does_not_emit_message_sent_without_correlation(self) -> None:
        broker, bot, handlers = _make_subscribed_broker_and_bot()
        bot.send_message.return_value = _fake_sent_message()

        cmd = CmdSendMessage(chat_id=-1001234567890, text="hi")
        await handlers["cmd.tg.send_message"](cmd)
        broker.publish.assert_not_awaited()

    async def test_telegram_error_does_not_emit_ack(self) -> None:
        broker, bot, handlers = _make_subscribed_broker_and_bot()
        bot.send_message.side_effect = _bad_request()

        cmd = CmdSendMessage(correlation_id=uuid4(), chat_id=1, text="x")
        await handlers["cmd.tg.send_message"](cmd)
        broker.publish.assert_not_awaited()


# ---------------------------------------------------------------------
# edit_message_text
# ---------------------------------------------------------------------


class TestEditMessageText:
    async def test_calls_bot(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        await handlers["cmd.tg.edit_message_text"](
            CmdEditMessageText(chat_id=1, message_id=7, text="new", parse_mode="HTML")
        )
        bot.edit_message_text.assert_awaited_once()

    async def test_swallows_message_not_modified(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        bot.edit_message_text.side_effect = _bad_request("Bad Request: message is not modified")
        # Не падаем
        await handlers["cmd.tg.edit_message_text"](
            CmdEditMessageText(chat_id=1, message_id=7, text="x")
        )


# ---------------------------------------------------------------------
# delete_message
# ---------------------------------------------------------------------


class TestDeleteMessage:
    async def test_calls_bot(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        await handlers["cmd.tg.delete_message"](CmdDeleteMessage(chat_id=1, message_id=42))
        bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=42)

    async def test_swallows_not_found(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        bot.delete_message.side_effect = _bad_request("message to delete not found")
        # Не падаем
        await handlers["cmd.tg.delete_message"](CmdDeleteMessage(chat_id=1, message_id=42))


# ---------------------------------------------------------------------
# answer_callback_query
# ---------------------------------------------------------------------


class TestAnswerCallbackQuery:
    async def test_calls_bot(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        await handlers["cmd.tg.answer_callback_query"](
            CmdAnswerCallbackQuery(callback_query_id="cb-1", text="ok", show_alert=False)
        )
        bot.answer_callback_query.assert_awaited_once()


# ---------------------------------------------------------------------
# pin_message
# ---------------------------------------------------------------------


class TestPinMessage:
    async def test_calls_bot(self) -> None:
        _, bot, handlers = _make_subscribed_broker_and_bot()
        await handlers["cmd.tg.pin_message"](
            CmdPinMessage(chat_id=1, message_id=7, disable_notification=True)
        )
        bot.pin_chat_message.assert_awaited_once_with(
            chat_id=1, message_id=7, disable_notification=True
        )
