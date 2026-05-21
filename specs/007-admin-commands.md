# 007. Admin-команды §3.7

**Status:** in-review (use-cases + handlers + integration-тесты ✅; ждёт gateway-tg для e2e)
**Author:** team
**Created:** 2026-05-21

## Why

Операторам нужен набор обслуживающих команд: переименовать заказчика, временно отключить/включить группу, перечитать список исполнителей, посмотреть всех зарегистрированных. По SPEC §3.7 эти команды работают **из любого чата**, ответ — в тот же чат, от не-исполнителей **молча игнорируются**.

## User flow

Исполнитель пишет команду в любом чате, где есть бот (группа заказчика, командная группа). Бот отвечает сообщением туда же.

- `/rename_customer <chat_id> "новое имя"` — переименовать заказчика
- `/deactivate_customer <chat_id>` — отключить (новые тикеты и меню перестают работать)
- `/activate_customer <chat_id>` — откатить
- `/reload_executors` — перечитать `config/executors.yaml` без рестарта
- `/list_customers` — список всех зарегистрированных групп с `chat_id`, `title`, `is_active`

Команды `/setup`, `/setup_team_group`, `/print_topic_id` тоже из §3.7, но они отдельные фичи (specs 005, 006).

## Technical flow

1. `tg_message` handler перехватывает событие с `text.startswith()` одной из 5 admin-команд (см. `ADMIN_COMMANDS` в [tg_message.py](../services/core/src/core/handlers/tg_message.py)).
2. **Единый guard**: загружаем `actor = ExecutorsRepository.get_by_telegram_id(event.user_id)`. Если `None` или `is_active=False` — молча выходим. Это перекликается с `/setup` / `/setup_team_group` / `/print_topic_id`, но дублируем сознательно: каждая ветка handler-а независима.
3. Соответствующий use-case (`RenameCustomer`, `DeactivateCustomer`, …) принимает `TgMessage`, парсит аргументы через `core.domain.admin`, и возвращает `AdminResult` с одной командой `cmd.tg.send_message` (ответ в тот же чат).
4. Идемпотентность — `processed_events.try_mark(event.event_id)` на каждом use-case.
5. Парсинг через `shlex.split` чтобы поддержать `"title with spaces"`.

### Deactivate-семантика

`is_active=False` → новых тикетов нет, меню не работает. Это реализовано отдельным шагом:

- В [`HandleMenuCallback.execute`](../services/core/src/core/services/handle_menu_callback.py) после загрузки `customer` добавлена проверка `if not customer.is_active: return Skipped("customer_inactive", answer=...)` с toast «Группа отключена».
- В [`CreateTicketPhase1.execute`](../services/core/src/core/services/create_ticket.py) после загрузки `customer` — `return TicketSkipped("customer_inactive")`. Сообщение заказчика всё равно будет удалено (логика handler-а: «вне creating_prompt — delete»), что соответствует UX «как будто ничего не произошло».
- **`CloseTicket` не блокируется** is_active — заказчик может закрыть существующий тикет в деактивированной группе. По SPEC §3.7: «существующие тикеты можно закрывать».

## Acceptance criteria

- [x] `/rename_customer <chat_id> "имя"` обновляет `customers.title`. *(test_happy_path)*
- [x] `/deactivate_customer <chat_id>` ставит `customers.is_active=false`. *(test_deactivate)*
- [x] `/activate_customer <chat_id>` ставит `customers.is_active=true`. *(test_activate)*
- [x] `/reload_executors` перечитывает `config/executors.yaml` без рестарта; missing-file ⇒ предупреждение. *(test_happy_path / test_missing_file)*
- [x] `/list_customers` показывает список с `chat_id`, `title`, `is_active`-маркером (✅/⛔). *(test_empty / test_lists_all)*
- [x] Команды от не-исполнителей молча игнорируются. *(executor-check в `_handle_admin_command` handler-а)*
- [x] Деактивированный заказчик: меню → toast «Группа отключена», создание тикета → skip. *(test_menu_callback_returns_inactive_toast / test_create_ticket_blocked_for_inactive)*
- [x] Idempotency: повтор `event_id` не повторяет действие. *(test_rename_idempotent)*
- [x] Неправильные аргументы → сообщение с usage, БД не меняется. *(test_invalid_args_shows_usage / test_unknown_customer)*

**Артефакты текущего шага:**
- [`core/domain/admin.py`](../services/core/src/core/domain/admin.py) — парсеры (через `shlex` для quoted strings) и тексты
- [`core/services/admin_commands.py`](../services/core/src/core/services/admin_commands.py) — 5 use-case'ов
- Расширение [`tg_message`](../services/core/src/core/handlers/tg_message.py) с веткой `_handle_admin_command` и общим executor-guard'ом
- `is_active`-проверки в `HandleMenuCallback` и `CreateTicketPhase1`
- 12 новых integration-тестов, всего 128 passing

## Out of scope

- `/setup`, `/setup_team_group`, `/print_topic_id` — реализованы в specs 005, 006
- Принудительное переназначение тикета / тимлид-функции — открытый вопрос §18.5, §18.6, не сейчас
