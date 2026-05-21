# 004. Close ticket

**Status:** in-review (use-case + handler + integration-тесты ✅; ждёт gateway-tg для e2e)
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

- [x] Кнопка «Закрыть тикет» нажата кем-то, кроме `created_by_user_id` → toast «Закрыть может только заказчик», без изменений в БД, без редактирования шапки. *(test_non_customer_cannot_initiate / test_non_customer_cannot_confirm)*
- [x] Заказчик жмёт «Закрыть» → шапка редактируется на «❓ Закрыть тикет? [Да, закрыть] [Отмена]». *(test_close_button_shows_confirm_dialog)*
- [x] «Отмена» → шапка возвращается в нормальный вид. *(test_cancel_restores_normal_header)*
- [x] «Да, закрыть» → `tickets.status=closed`, `closed_at` и `closed_by_user_id` заполнены атомарно. *(test_full_close_flow)*
- [x] Имя топика меняется на `[✅] #{id} {title}`. *(`format_topic_name(..., closed=True)`)*
- [x] Иконка топика → `TOPIC_ICON_CLOSED`. *(CmdEditForumTopic с icon_custom_emoji_id из settings.topic_icon_closed)*
- [x] Шапка обновляется в финальное состояние (без активных кнопок). *(`closed_header_keyboard()` → `{"inline_keyboard": []}`)*
- [x] В топик отправляется финальное сообщение «✅ Тикет закрыт. Спасибо!». *(CmdSendMessage)*
- [x] Топик закрывается через `closeForumTopic` (писать нельзя). *(CmdCloseForumTopic — последняя команда в результате, чтобы предыдущие правки успели уйти)*
- [x] Публикуется `events.ticket.closed`. *(TicketClosed с ticket_id, closed_by_user_id, closed_at)*
- [ ] sheets-sync обновляет строку (status, closed_at). **Не покрыто этой спекой — sheets-sync ещё не реализован.**
- [x] Idempotency: повторный callback `close_confirm` с тем же `event_id` не приводит к повторному закрытию. *(test_idempotency, test_already_closed)*

**Артефакты текущего шага:**
- [`close_ticket.py`](../services/core/src/core/services/close_ticket.py) — use-case с 3 ветками (close / close_cancel / close_confirm); проверка `from_user.id == created_by_user_id` обязательна на каждой ветке (повторный confirm от чужака отбракуется тоже)
- domain-хелперы в [`domain/ticket.py`](../services/core/src/core/domain/ticket.py): `confirm_close_keyboard`, `closed_header_keyboard`, `render_confirm_close_text`, `format_topic_name(..., closed=True)`
- multiplexer в [`tg_callback handler`](../services/core/src/core/handlers/tg_callback.py) теперь знает `close:`, `close_confirm:`, `close_cancel:`
- 17 новых integration-тестов: парсинг, permission denial, confirm-dialog, cancel-restore, full close + events + DB, idempotency, already_closed, close-из-new (без in_progress)
- Full suite: 96 passing, ruff clean, schema matches models

## Data changes

**Колонки:** `tickets.status`, `tickets.closed_at`, `tickets.closed_by_user_id`.

**События:** `events.ticket.closed`.

**Команды:** `cmd.tg.close_forum_topic`.

## Out of scope

- Reopen закрытого тикета — не поддерживается принципиально (SPEC §1, §6). Если нужно — заказчик создаёт новый тикет.

## Open questions

- Можно ли закрыть тикет в статусе `new` (до назначения)? — Да, без ограничений. Это валидный путь «передумал».
