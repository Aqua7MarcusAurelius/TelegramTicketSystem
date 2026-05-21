# 005. Onboard customer group

**Status:** draft
**Author:** team
**Created:** 2026-05-21

## Why

Должен быть простой способ подключить нового заказчика, не лазая в БД руками. Bot API не умеет создавать чаты, поэтому создание группы остаётся за человеком, а всё остальное — настройка General, регистрация в `customers`, проверка прав — автоматизируется.

## User flow

1. Ответственный сотрудник создаёт супергруппу в режиме форума (`Topics: ON`), называет `<Имя заказчика> — Тикеты`, приглашает заказчика обычным member'ом и исполнителей как анонимных админов.
2. Добавляет бота админом с правами `Manage Topics`, `Delete Messages`, `Pin Messages`.
3. Бот получает `my_chat_member` → автоматически проходит онбординг. Если не сработало — любой исполнитель пишет в General `/setup`, эффект тот же.
4. Результат: General переименован в `📋 Меню`, в нём запинено главное меню, General закрыт. Заказчик может создавать тикеты.

## Technical flow

См. [docs/SPEC.md §3.5](../docs/SPEC.md). Обработчик в core слушает `events.tg.bot_membership_changed` + admin-команду `/setup`.

Проверки (по порядку):
1. `chat_type == 'supergroup'` AND `is_forum == true` — иначе сообщение в General «Включите форум-режим, потом /setup».
2. Все нужные права бота (`can_manage_topics`, `can_delete_messages`, `can_pin_messages`) — иначе чек-лист недостающих + кнопка `🔄 Проверить ещё раз`.
3. `chat_id` не в `customers` — иначе «Уже подключено как '<title>'».

Onboarding:
- INSERT в `customers` (`title=chat.title`, `is_active=true`, `onboarded_at=now()`).
- `cmd.tg.edit_general_forum_topic` (name=`📋 Меню`).
- `cmd.tg.send_message` в General — рендер `MenuFSM.main`.
- `cmd.tg.pin_message` для меню → `customers.menu_message_id`.
- `cmd.tg.close_general_forum_topic`.
- Чистка системных сообщений (`forum_topic_edited`, `general_forum_topic_hidden` и т.п.).

## Acceptance criteria

- [ ] При получении `events.tg.bot_membership_changed` с `new_status=administrator` core запускает онбординг.
- [ ] Если `chat.is_forum=false` — бот пишет в General инструкцию включить форум-режим и выходит без записи в `customers`.
- [ ] Если каких-то прав у бота нет — бот выводит чек-лист недостающих прав и кнопку `🔄 Проверить ещё раз`. Нажатие кнопки повторно запускает onboarding.
- [ ] Если `chat_id` уже в `customers` — бот пишет «Уже подключено как '<title>'», ничего не пишет в БД.
- [ ] При успехе: INSERT в `customers`, General переименован в `📋 Меню`, главное меню запинено, General закрыт.
- [ ] `customers.menu_message_id` и `customers.onboarded_at` заполнены.
- [ ] Системные сообщения о close/edit General удаляются (по `service_message_type`).
- [ ] Команда `/setup` от исполнителя (`from_user.id` ∈ `executors`) приводит к тому же эффекту что автозапуск.
- [ ] Команда `/setup` от не-исполнителя молча игнорируется (никакого ответа).
- [ ] Если бота кикнули из группы (`new_status=left|kicked`) — пишется WARNING в `🤖 Логи`, `is_active` не меняется автоматически.

## Data changes

**Колонки:** `customers.menu_message_id`, `customers.onboarded_at`, `customers.is_active`.

**События:** `events.tg.bot_membership_changed`.

**Команды:** `cmd.tg.edit_general_forum_topic`.

## Out of scope

- Полноценный UI для деактивации (есть admin-команды `/deactivate_customer` / `/activate_customer` в spec'е admin-команд, §3.7)
- Множественные заказчики в группе (SPEC §18.11)

## Open questions

- Что если бот был добавлен админом, но без `can_post_messages`? — Сейчас в чек-листе не требуется. Подтвердить: для группы заказчика `can_post_messages` не нужен; для будущего канал-режима — потребуется. Оставляем как есть.
