# 005. Onboard customer group

**Status:** in-review (use-case + handlers + integration-тесты ✅; ждёт gateway-tg для e2e и `cmd.tg.get_chat_member` для полноценного recheck-кнопки)
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

- [x] При получении `events.tg.bot_membership_changed` с `new_status=administrator` core запускает онбординг. *([tg_bot_membership_changed handler](../services/core/src/core/handlers/tg_bot_membership_changed.py), test_happy_path)*
- [x] Если `chat.is_forum=false` — бот пишет в группу инструкцию включить форум-режим и выходит без записи в `customers`. *(test_not_forum_sends_instruction)*
- [x] Если каких-то прав у бота нет — бот выводит чек-лист недостающих прав и кнопку `🔄 Проверить ещё раз`. *(test_missing_rights_emits_checklist_with_button)*
- [~] Нажатие кнопки повторно запускает onboarding. **Частично:** без `cmd.tg.get_chat_member` (отсутствует в шине) use-case не может сам перепроверить права. Кнопка отвечает toast + сообщением «Изменение прав вызовет авто-проверку через my_chat_member, или нажмите /setup». Реальный реcheck отрабатывается следующим `my_chat_member` от Telegram. Подробности — в Architecture note ниже.
- [x] Если `chat_id` уже в `customers` (и `menu_message_id` заполнен) — бот пишет «Уже подключено как '<title>'», ничего не пишет в БД. *(test_already_registered_says_already_connected)*
- [x] При успехе: запись в `customers`, General переименован в `📋 Меню`, отправляется главное меню, General закрыт. *(test_happy_path)*
- [x] `customers.menu_message_id` (по `events.tg.message_sent`) и `customers.onboarded_at` заполнены. *(test_pins_menu_after_send + test_happy_path)*
- [x] Системные сообщения о close/edit General удаляются (по `service_message_type` ∈ {forum_topic_closed, forum_topic_reopened, general_forum_topic_unhidden, general_forum_topic_hidden, forum_topic_edited}). *([tg_message handler](../services/core/src/core/handlers/tg_message.py) — реализовано ещё в spec 002, охватывает onboarding-сообщения тоже)*
- [x] Команда `/setup` от исполнителя (`from_user.id` ∈ `executors`) приводит к тому же эффекту что автозапуск. *(test_emits_same_commands_as_membership_event + [tg_message _handle_setup_command](../services/core/src/core/handlers/tg_message.py))*
- [x] Команда `/setup` от не-исполнителя молча игнорируется. *(проверка `actor is None or not actor.is_active` в `_handle_setup_command`)*
- [x] Если бота кикнули из группы (`new_status=left|kicked`) — пишется WARNING в логи, `is_active` не меняется автоматически. *(`log_bot_kicked` + ветка в [tg_bot_membership_changed handler](../services/core/src/core/handlers/tg_bot_membership_changed.py))*

**Артефакты текущего шага:**
- Миграция [0004_customer_menu_correlation.py](../services/core/migrations/versions/20260521_0004_customer_menu_correlation.py) — `customers.menu_correlation_id UUID UNIQUE`
- Domain [onboarding.py](../services/core/src/core/domain/onboarding.py): `MissingRights`, тексты, клавиатура с `setup_recheck`
- [`OnboardCustomer`](../services/core/src/core/services/onboard_customer.py) — единая точка входа для membership-события и `/setup` команды; `HandleMenuMessageSent` (фаза 2)
- [`tg_bot_membership_changed`](../services/core/src/core/handlers/tg_bot_membership_changed.py) handler
- multiplexer в [`tg_callback`](../services/core/src/core/handlers/tg_callback.py) дополнен веткой `setup_recheck`
- `/setup` распознаётся в [`tg_message`](../services/core/src/core/handlers/tg_message.py) и проверяет `executors`
- 10 новых integration-тестов; всего 106 passing

## Architecture note: ограничение recheck-кнопки

Кнопка «🔄 Проверить ещё раз» по спеке должна **перепроверять текущие права бота**. Через шину получить актуальные права можно только запросив `getChatMember` у Telegram Bot API (gateway-tg → `cmd.tg.get_chat_member` → ответное событие).

В текущей версии:
- Этой команды в шине ещё нет (gateway-tg сам пока заглушка).
- Поэтому при нажатии кнопки бот: (а) отвечает toast `Обновите права бота — проверка запустится сама`, (б) отправляет в чат сообщение с тем же текстом. Когда пользователь изменит права, Telegram пришлёт новый `my_chat_member`, onboarding запустится автоматически. Если пользователь хочет принудительно — ему остаётся `/setup`.

Когда будем делать gateway-tg, добавим `cmd.tg.get_chat_member` + `events.tg.chat_member_response` и заменим заглушку на полноценный self-recheck. Это отмечено как известное ограничение, AC «нажатие кнопки повторно запускает onboarding» помечен `~`.

## Data changes

**Колонки:** `customers.menu_message_id`, `customers.onboarded_at`, `customers.is_active`.

**События:** `events.tg.bot_membership_changed`.

**Команды:** `cmd.tg.edit_general_forum_topic`.

## Out of scope

- Полноценный UI для деактивации (есть admin-команды `/deactivate_customer` / `/activate_customer` в spec'е admin-команд, §3.7)
- Множественные заказчики в группе (SPEC §18.11)

## Open questions

- Что если бот был добавлен админом, но без `can_post_messages`? — Сейчас в чек-листе не требуется. Подтвердить: для группы заказчика `can_post_messages` не нужен; для будущего канал-режима — потребуется. Оставляем как есть.
