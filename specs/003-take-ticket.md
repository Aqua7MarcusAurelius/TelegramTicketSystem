# 003. Take ticket (assign executor)

**Status:** draft
**Author:** team
**Created:** 2026-05-21

## Why

Без назначения тикет не может перейти в работу. Любой исполнитель (включая тимлида) должен мочь взять себе или передать коллеге одним нажатием в `🆕 Входящие` командной группы — это сознательная симметрия (self-pickup и lead-assign — один интерфейс, см. SPEC §8.2).

## User flow

1. В `🆕 Входящие` появляется сообщение бота: «🆕 Новый тикет #N, Заказчик: …, Тема: …, Кто берёт?» + ряд inline-кнопок с именами активных исполнителей + `🔗 Открыть тикет` (URL deep-link).
2. Исполнитель A жмёт кнопку с именем B (B может равняться A).
3. Бот обновляет сообщение: кнопки имён пропадают, остаётся «✅ Взят: B» и `🔗 Открыть тикет`.
4. В группе заказчика иконка тикетного топика меняется на 🟡, шапка обновляется (статус «🟡 В работе», исполнитель — анонимный «Команда поддержки»).

## Technical flow

1. `notifications` подписан на `events.ticket.created` → формирует сообщение и публикует `cmd.tg.send_message` в командную группу, топик `EXECUTOR_GROUP_TOPIC_INCOMING`. Кнопки: `callback_data = "assign:<ticket_id>:<executor_user_id>"`. Сохраняет `message_id` в `notification_log`.
2. `events.tg.callback` → `core` парсит callback_data, проверяет:
   - `from_user.id` ∈ активных исполнителей (`executors.is_active=true` AND `telegram_user_id IS NOT NULL`) → иначе toast «Вы не в списке исполнителей».
   - `tickets.assignee_id IS NULL` → иначе toast «Уже взят: <имя>».
3. Транзакция: UPDATE `tickets` (assignee_id, status=`in_progress`, in_progress_at=now()) + INSERT в `ticket_events` `{event_type: 'assigned', actor_user_id: A, payload: {assignee_id: B}}`.
4. Публикации:
   - `cmd.tg.edit_message_text` (уведомление во `Входящих` — обновляется).
   - `cmd.tg.edit_forum_topic` (иконка топика → `TOPIC_ICON_IN_PROGRESS`).
   - `cmd.tg.edit_message_text` (шапка тикетного топика).
   - `events.ticket.assigned`.

## Acceptance criteria

- [ ] В уведомлении показываются кнопки с именами всех активных исполнителей, по 3 в ряд, в порядке как в `executors.yaml`.
- [ ] Исполнитель из YAML, у которого ещё не резолвлен `telegram_user_id`, кнопкой не показывается; в логи пишется WARNING.
- [ ] Callback с `callback_data` вида `assign:<ticket_id>:<executor_id>` обрабатывается только если `from_user.id` есть среди активных исполнителей; иначе toast «Вы не в списке исполнителей», без изменений в БД.
- [ ] Любой исполнитель может назначить как себя (A == B), так и другого (A != B) — один интерфейс.
- [ ] Race: если тикет уже назначен — toast «Уже взят: <имя>», БД не меняется.
- [ ] При успехе: `tickets.assignee_id`, `tickets.status='in_progress'`, `tickets.in_progress_at` обновлены атомарно.
- [ ] В `ticket_events` записана запись с `actor_user_id` (кто нажал) и `assignee_id` в payload.
- [ ] Уведомление во `Входящих` редактируется: кнопки имён убираются, текст содержит «✅ Взят: <имя>», остаётся `[🔗 Открыть тикет]`.
- [ ] Иконка тикетного топика → `TOPIC_ICON_IN_PROGRESS`.
- [ ] Шапка тикетного топика обновляется (статус «🟡 В работе», исполнитель — «Команда поддержки» — заказчик не видит имени).
- [ ] Публикуется `events.ticket.assigned` с `assignee_user_id` и `assigned_by_user_id`.
- [ ] sheets-sync обновляет строку (assignee, in_progress_at).
- [ ] Idempotency: повторный `events.tg.callback` с тем же `event_id` не делает повторного назначения.

## Data changes

**События:** `events.ticket.assigned` (новые поля `assignee_user_id`, `assigned_by_user_id`).

**Команды:** `cmd.tg.edit_forum_topic`.

**Таблицы:**
- `executors` (id, telegram_user_id, username, full_name, is_active, is_lead, created_at)
- `notification_log` (id, event_id, kind, target_chat_id, target_topic_id, message_id, sent_at, status, error)

## Out of scope

- Переназначение и отмена назначения (SPEC §8.3, §18.5)
- Эскалации (SPEC §18.7)

## Open questions

- Что делать, если все активные исполнители не имеют `telegram_user_id`? — Уведомление публикуется без кнопок назначения, в `🤖 Логи` пишется ERROR. Принимаем.
