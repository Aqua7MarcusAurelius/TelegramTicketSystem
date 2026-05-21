# 001. Customer menu (single-message UI в General)

**Status:** draft
**Author:** team
**Created:** 2026-05-21

## Why

Заказчику нужна единая точка входа во все взаимодействия с ботом, без перехода в ЛС и без отдельных команд. Запиненное сообщение бота в General группы заказчика — это его «приборная панель». Без меню никакие другие сценарии (создать тикет, посмотреть свои) не запустятся, поэтому это первая фича.

## User flow

1. В группе заказчика в General висит запиненное сообщение бота — «📋 Меню».
2. Заказчик жмёт inline-кнопку («🆕 Новый тикет», «📋 Мои тикеты», «❓ Помощь»).
3. Бот редактирует тот же message — переключает на нужный экран (`creating_prompt`, `my_tickets`, `closed_tickets`, `help`).
4. Из любого экрана есть кнопка возврата `[⬅️ Назад]` в `main`.

Подробное содержимое экранов — см. [docs/SPEC.md §7.1](../docs/SPEC.md).

## Technical flow

1. Источник меню-сообщения создаётся при onboarding'е группы заказчика (см. spec [005](005-onboard-customer.md)). Эта спека предполагает, что `customers.menu_message_id` уже заполнен.
2. На `events.tg.callback` core определяет, что callback пришёл от меню (по содержимому `callback_data: menu:<action>`), и принимает решение по FSM.
3. Состояния FSM хранятся в `fsm_state` (Postgres) и зеркалятся в Redis db=1 (для быстрого доступа). Ключ — `(user_id, chat_id)`. Состояние и его данные — JSONB.
4. Переключения экранов — публикация `cmd.tg.edit_message_text` с новым `text` и `reply_markup`. Меню никогда не пересоздаётся через `sendMessage` — только редактируется.
5. На каждый callback бот публикует `cmd.tg.answer_callback_query` (в 3 секунды).

## Acceptance criteria

- [ ] При onboarding'е (spec 005) меню-сообщение создано и его `message_id` записан в `customers.menu_message_id`. General закрыт.
- [ ] Экраны: `main`, `creating_prompt`, `my_tickets`, `closed_tickets`, `help` — каждый имеет собственный набор кнопок и текста, описанный в SPEC §7.1.
- [ ] Переключение между экранами реализовано через `cmd.tg.edit_message_text` (никогда `sendMessage`).
- [ ] FSM-состояние per `(user_id, chat_id)` хранится в `fsm_state` (Postgres) с TTL через `expires_at`.
- [ ] При нажатии callback'а бот публикует `cmd.tg.answer_callback_query` в течение 3 сек (тест: на mock-bus коллбэк-команда появляется не позже 3 сек после `TgCallback`).
- [ ] Список «📋 Мои тикеты» показывает только тикеты текущего `user_id` в этой группе. Каждая строка — кнопка с URL-deep-link на тикетный топик.
- [ ] «🗂 Закрытые» показывает тикеты в статусе `closed` за последние 30 дней.
- [ ] При >10 тикетах в списке — пагинация (`◀️` `▶️`).
- [ ] Если заказчик пишет в General произвольный текст вне состояния `creating_prompt` — сообщение удаляется (`cmd.tg.delete_message`), меню не меняется.

## Data changes

**Новые таблицы:**
- `customers` (минимум: `id`, `telegram_chat_id`, `title`, `is_active`, `menu_message_id`, `onboarded_at`, `created_at`)
- `fsm_state` (`user_id`, `chat_id`, `state`, `data jsonb`, `updated_at`, `expires_at`)
- `core_processed_events` (`event_id`, `processed_at`)

**Новые команды шины:** `cmd.tg.edit_message_text`, `cmd.tg.delete_message`, `cmd.tg.answer_callback_query`.

DDL — в SPEC §10.2.

## Out of scope

- Создание тикета (spec [002](002-create-ticket.md))
- Onboarding группы (spec [005](005-onboard-customer.md))
- Контент help-экрана — заглушка с TODO (SPEC §18.2)

## Open questions

- Деталей рендера «Мои тикеты» при пустом списке: показывать «Тикетов пока нет» или сразу возвращать в `main` с тостом? — Принимаем «показывать пустой экран с кнопкой назад».
