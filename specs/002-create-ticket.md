# 002. Create ticket

**Status:** draft
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

- [ ] Callback `menu:new_ticket` приводит к публикации `cmd.tg.reopen_general_forum_topic` и переходу FSM в `creating_prompt`.
- [ ] FSM-состояние `creating_prompt` имеет TTL = 120 сек (`expires_at`).
- [ ] Таймаут: если за 120 сек не пришло сообщение, FSM возвращается в `main`, публикуется `cmd.tg.close_general_forum_topic`, меню обновляется тостом «⏱ Время вышло».
- [ ] При получении ответа создаётся `tickets`-запись и форум-топик с именем `#{id} {title}` и иконкой `TOPIC_ICON_NEW`.
- [ ] Шапка тикетного топика создаётся согласно SPEC §7.3 и пинится; `tickets.header_message_id` заполнен.
- [ ] Сообщение заказчика в General удаляется.
- [ ] Системные сообщения о open/close General удаляются (на основании `service_message_type ∈ {forum_topic_closed, forum_topic_reopened, general_forum_topic_unhidden, general_forum_topic_hidden}`).
- [ ] Меню возвращается в `main` с тостом «✅ Тикет #{id} создан».
- [ ] Публикуется `events.ticket.created` с полным набором полей из `TicketCreated`.
- [ ] Tикет.title = первая строка ответа, обрезанная до 128 символов (лимит Telegram на имя топика). Description — остаток.
- [ ] sheets-sync добавляет новую строку (verifiable e2e против мока gspread).
- [ ] Idempotency: повторная обработка `events.tg.message` (тот же `event_id`) не создаёт второй тикет.

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
