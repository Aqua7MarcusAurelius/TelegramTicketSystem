# 001. Customer menu (single-message UI в General)

**Status:** in-review (FSM + use-case + integration-тесты ✅; рендер меню в Telegram — после spec 005)
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

- [ ] При onboarding'е (spec 005) меню-сообщение создано и его `message_id` записан в `customers.menu_message_id`. General закрыт. **Зависит от spec 005.**
- [x] Экраны: `main`, `creating_prompt`, `my_tickets`, `closed_tickets`, `help` — каждый имеет собственный набор кнопок и текста, описанный в SPEC §7.1. *([core/domain/menu_render.py](../services/core/src/core/domain/menu_render.py))*
- [x] Переключение между экранами реализовано через `cmd.tg.edit_message_text` (никогда `sendMessage`). *(use-case всегда возвращает CmdEditMessageText, не CmdSendMessage — [handle_menu_callback.py](../services/core/src/core/services/handle_menu_callback.py))*
- [x] FSM-состояние per `(user_id, chat_id)` хранится в `fsm_state` (Postgres) с TTL через `expires_at`. *([repository/fsm.py](../services/core/src/core/repository/fsm.py), миграция [0001_initial_core](../services/core/migrations/versions/20260521_0001_initial_core_schema.py))*
- [x] При нажатии callback'а бот публикует `cmd.tg.answer_callback_query` в течение 3 сек. *(use-case всегда возвращает answer в `MenuCallbackResult` или в `Skipped.answer`; время отклика — синхронный путь handler'а)*
- [x] Список «📋 Мои тикеты» показывает только тикеты текущего `user_id` в этой группе. Каждая строка — кнопка с URL-deep-link на тикетный топик. *([test_lists_only_current_user_active_tickets](../tests/integration/test_menu_callback.py))*
- [ ] «🗂 Закрытые» показывает тикеты в статусе `closed` за последние 30 дней. *(сейчас фильтр по статусу есть, фильтр «за 30 дней» — TODO к spec 002+, когда появятся реальные `closed_at`)*
- [x] При >10 тикетах в списке — пагинация (`◀️` `▶️`). *([test_pagination_when_more_than_page_size](../tests/integration/test_menu_callback.py))*
- [ ] Если заказчик пишет в General произвольный текст вне состояния `creating_prompt` — сообщение удаляется (`cmd.tg.delete_message`), меню не меняется. **Реализуется вместе с spec 002 (там же добавляется handler на `events.tg.message`).**

**Артефакты текущего шага:**
- 29 unit-тестов на чистый FSM ([tests/unit/test_menu_fsm.py](../tests/unit/test_menu_fsm.py))
- 11 integration-тестов use-case против реального Postgres ([tests/integration/test_menu_callback.py](../tests/integration/test_menu_callback.py))
- Initial Alembic migration [0001_initial_core_schema.py](../services/core/migrations/versions/20260521_0001_initial_core_schema.py) (включает DDL всех core-таблиц, чтобы spec'ы 002..004 не пересоздавали схему)

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
