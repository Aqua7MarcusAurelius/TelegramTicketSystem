# 004. Close ticket

**Status:** draft
**Author:** team
**Created:** 2026-05-21

## Why

Завершение жизненного цикла. Закрытие — единственная привилегированная операция заказчика (только он сам, не исполнители — см. SPEC §2). Реоупен не предусмотрен: если что-то всплыло — новый тикет.

## User flow

1. В тикетном топике заказчик жмёт `✅ Закрыть тикет` на запиненной шапке.
2. Бот показывает подтверждение: «Уверены? [Да, закрыть] [Отмена]».
3. `Да` → топик переименован в `[✅] #{id} {title}`, иконка `TOPIC_ICON_CLOSED`, шапка обновлена в финальное состояние, в топик отправлено финальное «Тикет закрыт. Спасибо!», топик закрыт.
4. `Отмена` → шапка возвращается в нормальный вид с кнопкой `✅ Закрыть тикет`.

## Technical flow

1. `events.tg.callback` `callback_data = "close:<ticket_id>"` → core:
   - Проверяет `from_user.id == tickets.created_by_user_id`. Если нет — toast «Закрыть тикет может только заказчик», выход.
   - Публикует `cmd.tg.edit_message_text` для шапки с подтверждением (`callback_data = "close_confirm:<ticket_id>"`, `close_cancel:<ticket_id>"`).
2. `close_cancel:<ticket_id>` → возвращает шапку к нормальному виду.
3. `close_confirm:<ticket_id>`:
   - Проверка снова (`from_user.id == created_by_user_id`).
   - UPDATE `tickets` (status=`closed`, closed_at=now(), closed_by_user_id) + INSERT `ticket_events`.
   - Публикации:
     - `cmd.tg.edit_forum_topic` (name=`[✅] #{id} {title}`, icon=`TOPIC_ICON_CLOSED`).
     - `cmd.tg.send_message` («Тикет закрыт. Спасибо!»).
     - `cmd.tg.close_forum_topic`.
     - `cmd.tg.edit_message_text` (шапка финал).
     - `events.ticket.closed`.

## Acceptance criteria

- [ ] Кнопка «Закрыть тикет» на шапке нажата кем-то, кроме `created_by_user_id` → toast «Закрыть может только заказчик», без изменений в БД, без редактирования шапки.
- [ ] Заказчик жмёт «Закрыть» → шапка редактируется на «Уверены? [Да, закрыть] [Отмена]».
- [ ] «Отмена» → шапка возвращается в нормальный вид.
- [ ] «Да, закрыть» → `tickets.status=closed`, `closed_at` и `closed_by_user_id` заполнены атомарно.
- [ ] Имя топика меняется на `[✅] #{id} {title}`.
- [ ] Иконка топика → `TOPIC_ICON_CLOSED`.
- [ ] Шапка обновляется в финальное состояние (без активных кнопок).
- [ ] В топик отправляется финальное сообщение «Тикет закрыт. Спасибо!».
- [ ] Топик закрывается через `closeForumTopic` (писать нельзя).
- [ ] Публикуется `events.ticket.closed`.
- [ ] sheets-sync обновляет строку (status, closed_at).
- [ ] Idempotency: повторный callback `close_confirm` с тем же `event_id` не приводит к повторному закрытию (тикет уже `closed` → toast «Тикет уже закрыт»).

## Data changes

**Колонки:** `tickets.status`, `tickets.closed_at`, `tickets.closed_by_user_id`.

**События:** `events.ticket.closed`.

**Команды:** `cmd.tg.close_forum_topic`.

## Out of scope

- Reopen закрытого тикета — не поддерживается принципиально (SPEC §1, §6). Если нужно — заказчик создаёт новый тикет.

## Open questions

- Можно ли закрыть тикет в статусе `new` (до назначения)? — Да, без ограничений. Это валидный путь «передумал».
