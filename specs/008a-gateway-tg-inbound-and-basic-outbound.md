# 008a. gateway-tg: inbound + базовый outbound

**Status:** in-review (dev-режим работает; форум-операции и webhook — в 8b)
**Author:** team
**Created:** 2026-05-21

## Why

До этой спеки `gateway-tg` был заглушкой: бизнес-логика в `core` гоняется через шину, но Telegram её не видит, а core не получает реальных обновлений. Чтобы можно было хоть как-то потыкать систему в dev (long-poll), нужно:

1. Подписаться на updates от Telegram через aiogram, конвертировать их в `events.tg.*`.
2. Подписаться на `cmd.tg.*` из шины и реально вызывать методы Bot API.
3. Уметь возвращать ответы наружу там, где они есть (`events.tg.message_sent` для команд с `correlation_id`).

8a покрывает inbound целиком + 5 базовых outbound-команд: `send_message`, `edit_message_text`, `delete_message`, `answer_callback_query`, `pin_message`. Форум-операции (`create/edit/close/reopen_forum_topic`, `_general_forum_topic`) и webhook-prod-режим — в **8b**.

## User flow

Локально:

```bash
# 1) ставим BOT_TOKEN из @BotFather в .env, у бота выключаем privacy mode
# 2) docker compose up -d postgres redis
# 3) docker compose exec core alembic upgrade head
# 4) запускаем gateway-tg и core
```

После старта:

- Любое сообщение в чате с ботом превращается в `events.tg.message` и попадает в `core`.
- Любой нажатый inline-callback — в `events.tg.callback`.
- Добавление/удаление бота админом — в `events.tg.bot_membership_changed`.

Когда `core` публикует `cmd.tg.send_message` (с `correlation_id`) — gateway-tg реально отправляет, ловит ответный `Message` и публикует `events.tg.message_sent` с тем же `correlation_id`. Это закрывает третью фазу `create_ticket`, фазу 2 `onboard_customer` и подсадку `inbox_message_id` в spec 003.

## Technical flow

### Inbound

[`build_dispatcher`](../services/gateway-tg/src/gateway_tg/inbound/dispatcher.py) собирает aiogram `Dispatcher` и регистрирует 4 handler'а: `message`, `edited_message`, `callback_query`, `my_chat_member`. Каждый handler — тонкая обёртка над чистой функцией из [`inbound/converters.py`](../services/gateway-tg/src/gateway_tg/inbound/converters.py):

- `message_to_event(Message) → TgMessage`: разбирает чат, пользователя, текст/caption, определяет `is_anonymous_admin` через `sender_chat == chat`, и сканирует 23 атрибута на наличие service-message (forum/general topic операции, pinned_message, video chat и т.д.).
- `callback_to_event(CallbackQuery) → TgCallback | None`: фильтрует callbacks без message/data.
- `my_chat_member_to_event(ChatMemberUpdated) → TgBotMembershipChanged`: переход old → new статуса бота, плюс права (`can_manage_topics`, `can_delete_messages`, `can_pin_messages`).

### Outbound

[`register(broker, bot)`](../services/gateway-tg/src/gateway_tg/outbound/executor.py) подписывается на 5 стримов и для каждого вызывает соответствующий метод `aiogram.Bot`. `reply_markup` в шине хранится как dict — конвертируется в `InlineKeyboardMarkup` через [`mappers.to_inline_keyboard`](../services/gateway-tg/src/gateway_tg/outbound/mappers.py).

Ключевая особенность `_send_message`: если у команды выставлен `correlation_id`, после успешной отправки публикуется `events.tg.message_sent` c этим же `correlation_id` и `message_id` из ответа. Это закрывает обратную связь для трёх фаз в core.

`TelegramBadRequest` мы НЕ переэмитим как ошибку — для частых случаев (`message is not modified`, `message to delete not found`, `query is too old`) пишем только `debug`-лог.

### main.py

`gateway-tg/main.py` стартует параллельно: FastStream-app (для подписок на `cmd.tg.*`) и `dp.start_polling(bot)` как отдельный asyncio-task. При остановке корректно отменяет polling и закрывает `bot.session`.

## Acceptance criteria

- [x] aiogram-Dispatcher собирается, 4 типа updates конвертируются в `events.tg.*`. *(15 unit-тестов в [test_inbound_converters.py](../tests/unit/test_inbound_converters.py))*
- [x] Анонимный админ детектится через `sender_chat == chat`. *(test_anonymous_admin_detected)*
- [x] Service-messages о forum/general topic определяются — нужны для spec 002 (cleanup). *(test_service_forum_topic_closed, test_service_general_forum_topic_hidden + параметризованный)*
- [x] `caption` подхватывается, если `text` пустой. *(test_caption_fallback_when_text_none)*
- [x] callback без message или без data → не публикуем, отвечаем тостом-noop. *(test_without_message_returns_none, test_without_data_returns_none)*
- [x] `my_chat_member`: переходы member ↔ administrator, права берутся из new_chat_member. *(test_promoted_to_administrator, test_demoted_to_member)*
- [x] Все 5 базовых cmd.tg.* команд выполняют соответствующие методы Bot API с правильными аргументами. *(14 unit-тестов в [test_outbound_executor.py](../tests/unit/test_outbound_executor.py))*
- [x] `cmd.tg.send_message` с `correlation_id` → публикуется `events.tg.message_sent`. *(test_emits_message_sent_when_correlation_present)*
- [x] `cmd.tg.send_message` без `correlation_id` → ничего не публикуется. *(test_does_not_emit_message_sent_without_correlation)*
- [x] Ошибка Bot API → пишем лог, не падаем, ack не эмитим. *(test_telegram_error_does_not_emit_ack, test_swallows_message_not_modified, test_swallows_not_found)*
- [x] Long-poll стартует параллельно с FastStream-подписчиками; при остановке корректно cancel'ится. *(реализовано в [main.py](../services/gateway-tg/src/gateway_tg/main.py); поведение опирается на aiogram и проверяется руками в dev)*
- [ ] Webhook prod-mode. **Отложено в 8b.**
- [ ] Форум-операции (`createForumTopic`, `editForumTopic`, `closeForumTopic`, `reopenForumTopic`, `editGeneralForumTopic`, `closeGeneralForumTopic`, `reopenGeneralForumTopic`) — публикация `events.tg.topic_created`. **Отложено в 8b.**

**Артефакты текущего шага:**
- [`inbound/converters.py`](../services/gateway-tg/src/gateway_tg/inbound/converters.py) + [`dispatcher.py`](../services/gateway-tg/src/gateway_tg/inbound/dispatcher.py)
- [`outbound/executor.py`](../services/gateway-tg/src/gateway_tg/outbound/executor.py) + [`mappers.py`](../services/gateway-tg/src/gateway_tg/outbound/mappers.py)
- [`main.py`](../services/gateway-tg/src/gateway_tg/main.py) с long-poll
- 29 новых unit-тестов; всего 157 passing

## Out of scope (→ 8b)

- 7 форум-команд `cmd.tg.create_forum_topic` / `cmd.tg.edit_forum_topic` / `cmd.tg.close_forum_topic` / `cmd.tg.reopen_forum_topic` / `cmd.tg.edit_general_forum_topic` / `cmd.tg.close_general_forum_topic` / `cmd.tg.reopen_general_forum_topic`
- Эмиссия `events.tg.topic_created` (ответ Bot API на `createForumTopic`)
- Webhook prod-режим: HTTP-сервер, signature-check, registerWebhook на старте
- Полноценный self-recheck кнопки `setup_recheck` (требует `cmd.tg.get_chat_member`)
