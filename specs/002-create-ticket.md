# 002. Create ticket

**Status:** in-review (use-case'ы + handlers + integration-тесты ✅; ждёт gateway-tg для e2e через реальный Bot API)
**Author:** team
**Created:** 2026-05-21

## Why

Главная операция системы. Заказчик из General группы создаёт тикет, бот в фоне поднимает форум-топик, чистит за собой системные сообщения и возвращает меню в исходное состояние. Без этой фичи всё остальное не имеет смысла.

## User flow

1. В `📋 Меню` заказчик жмёт `🆕 Новый тикет`.
2. Бот открывает General (на время ввода) и показывает в меню промпт «Опишите задачу одним сообщением 👇» + кнопку `❌ Отмена`.
3. Заказчик пишет одно сообщение с описанием в General.
4. Бот удаляет сообщение заказчика и системные «топик открыт/закрыт», возвращает General в закрытое состояние, меню в `main` с тостом «✅ Тикет #{id} создан».
5. В новом топике бот пинит шапку тикета (см. SPEC §7.3).

## Technical flow

См. [docs/SPEC.md §7.2](../docs/SPEC.md). Основные шаги внутри core:

1. На callback `menu:new_ticket` core переводит FSM в `creating_prompt` с `expires_at = now() + 2 min`.
2. Публикует `cmd.tg.reopen_general_forum_topic` + `cmd.tg.edit_message_text` (промпт).
3. На следующее `events.tg.message` от того же `user_id` в этом `chat_id` в состоянии `creating_prompt`:
   - INSERT в `tickets` (status=`new`, assignee_id=NULL).
   - `cmd.tg.create_forum_topic` — ждёт `events.tg.topic_created` с тем же `correlation_id`, пишет `tickets.topic_id`.
   - `cmd.tg.send_message` в новый топик — шапка → `cmd.tg.pin_message`, сохраняет `header_message_id`.
   - `cmd.tg.delete_message` (исходное сообщение заказчика).
   - `cmd.tg.close_general_forum_topic`.
   - Чистит системные сообщения о open/close (по `service_message_type` в `TgMessage`).
   - `cmd.tg.edit_message_text` (меню в `main` с тостом).
   - Публикует `events.ticket.created`.

## Acceptance criteria

- [x] Callback `menu:new_ticket` приводит к публикации `cmd.tg.reopen_general_forum_topic` и переходу FSM в `creating_prompt`. *([handle_menu_callback.py](../services/core/src/core/services/handle_menu_callback.py), test_main_to_creating_prompt_reopens_general)*
- [x] FSM-состояние `creating_prompt` имеет TTL = 120 сек (`expires_at`). *(test_main_to_creating_prompt_sets_ttl)*
- [x] Таймаут: если за 120 сек не пришло сообщение, FSM возвращается в `main`, публикуется `cmd.tg.close_general_forum_topic`, меню обновляется тостом «⏱ Время вышло». *([expire_creating_prompt.py](../services/core/src/core/services/expire_creating_prompt.py) + фоновый цикл в [main.py](../services/core/src/core/main.py); test_returns_close_and_edit_commands_for_expired)*
- [x] При получении ответа создаётся `tickets`-запись и форум-топик с именем `#{id} {title}` и иконкой `TOPIC_ICON_NEW`. *(CreateTicketPhase1 + HandleTopicCreated; test_happy_path_creates_ticket_and_emits_4_commands)*
- [x] Шапка тикетного топика создаётся согласно SPEC §7.3 и пинится; `tickets.header_message_id` заполнен. *(HandleHeaderMessageSent через новое событие `events.tg.message_sent`; test_pins_header_and_publishes_ticket_created)*
- [x] Сообщение заказчика в General удаляется. *(CreateTicketPhase1 эмитит CmdDeleteMessage; test_happy_path_creates_ticket_and_emits_4_commands)*
- [x] Системные сообщения о open/close General удаляются. *([tg_message handler](../services/core/src/core/handlers/tg_message.py) фильтрует по `service_message_type` ∈ {forum_topic_closed, forum_topic_reopened, general_forum_topic_unhidden, general_forum_topic_hidden, forum_topic_edited})*
- [x] Меню возвращается в `main` с тостом «✅ Тикет #{id} создан». *(CmdEditMessageText в результате CreateTicketPhase1)*
- [x] Публикуется `events.ticket.created` с полным набором полей из `TicketCreated`. *(в фазе 3 после получения header message_id)*
- [x] Ticket.title = первая строка ответа, обрезанная до 128 символов; description — остаток. *([domain/ticket.py](../services/core/src/core/domain/ticket.py); 11 unit-тестов в [tests/unit/test_ticket_parsing.py](../tests/unit/test_ticket_parsing.py))*
- [ ] sheets-sync добавляет новую строку. **Не покрыто этой спекой — sheets-sync ещё не реализован, эта AC переезжает в spec для sheets-sync.**
- [x] Idempotency: повторная обработка `events.tg.message` (тот же `event_id`) не создаёт второй тикет. *(test_idempotency_on_repeat_event_id, ON CONFLICT DO NOTHING в core_processed_events)*

**Артефакты текущего шага:**
- Миграция [0002_ticket_create_correlation.py](../services/core/migrations/versions/20260521_0002_ticket_create_correlation.py) — `tickets.topic_id` стал nullable, добавлена `tickets.create_correlation_id`
- Новое событие шины `events.tg.message_sent` ([shared/events/tg.py](../shared/src/shared/events/tg.py)) — нужно gateway-tg для возврата `message_id` после `cmd.tg.send_message` с `correlation_id`
- 11 unit-тестов парсера, 14 integration-тестов для всех трёх фаз + expire-loop
- Stream dispatcher [shared/events/dispatch.py](../shared/src/shared/events/dispatch.py) — единый маппинг типа события → имя стрима

## Architecture note: трёхфазное создание

Telegram Bot API асинхронен (response → отдельные events), поэтому создание тикета не помещается в один use-case:

1. **Фаза 1** — `CreateTicketPhase1` на `events.tg.message`. INSERT в `tickets` с `topic_id=NULL` и `create_correlation_id=UUID`, эмиссия 4 команд.
2. **Фаза 2** — `HandleTopicCreated` на `events.tg.topic_created` (correlation_id = тот же UUID). UPDATE `tickets.topic_id`, эмиссия `cmd.tg.send_message` с шапкой (correlation_id = тот же).
3. **Фаза 3** — `HandleHeaderMessageSent` на `events.tg.message_sent` (correlation_id = тот же UUID). UPDATE `tickets.header_message_id`, эмиссия `cmd.tg.pin_message` и **публикация `events.ticket.created`** (потребители уже видят корректный `topic_id`).

`tickets.create_correlation_id UNIQUE` обеспечивает однозначную связь команд и ответов; идемпотентность каждой фазы — через `processed_events.try_mark()`.

## Data changes

**Новые таблицы:**
- `tickets` (id, customer_id, topic_id, header_message_id, title, description, status, assignee_id, created_by_user_id, created_at, in_progress_at, closed_at, closed_by_user_id, UNIQUE(customer_id, topic_id))
- `ticket_events` (id, ticket_id, event_type, payload jsonb, actor_user_id, created_at)
- `ticket_status` ENUM (`new`, `in_progress`, `closed`)

**Новое событие:** `events.ticket.created`.

**Новые команды:** `cmd.tg.create_forum_topic`, `cmd.tg.close_general_forum_topic`, `cmd.tg.reopen_general_forum_topic`, `cmd.tg.pin_message`.

## Out of scope

- Назначение исполнителя (spec [003](003-take-ticket.md))
- Закрытие тикета (spec [004](004-close-ticket.md))
- Уведомление в `🆕 Входящие` — оно публикуется через `events.ticket.created`, но текст шаблона и обработка — в spec 003

## Open questions

- Что показывать заказчику, если бот не смог создать топик (например, превышен лимит топиков в группе)? — Пишем в `🤖 Логи` командной группы, заказчику в `main` тост «⚠️ Не удалось создать тикет, обратитесь к команде».
