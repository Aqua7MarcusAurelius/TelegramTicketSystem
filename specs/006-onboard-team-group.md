# 006. Onboard team group (one-time)

**Status:** in-review (use-case + handlers + integration-тесты ✅; ждёт gateway-tg для e2e)
**Author:** team
**Created:** 2026-05-21

## Why

Одноразовая настройка командной группы без редактирования БД и без ручного гадания, какому топику соответствует какой `message_thread_id`. Бот сам создаёт топики и выдаёт готовый блок env-переменных для копирования.

## User flow

1. Любой админ из `executors.yaml` создаёт форум-группу `<Имя команды> — Backoffice`, добавляет всех исполнителей и бота как админа.
2. Пишет в любом чате с ботом `/setup_team_group`.
3. Бот создаёт топики `🆕 Входящие`, `🚨 Эскалации`, `📊 Сводка`, `🤖 Логи` и выводит готовый блок `EXECUTOR_GROUP_*` для копирования в `.env`.
4. Админ копирует, кладёт в `.env`, перезапускает сервисы (`docker compose restart`).
5. На старте core валидирует наличие топиков (см. SPEC §3.6) и продолжает работу.

## Technical flow

См. [docs/SPEC.md §3.6](../docs/SPEC.md).

1. На `events.tg.message` с `text` начинающимся с `/setup_team_group` и `from_user.id` ∈ `executors`:
   - Проверка: `chat.is_forum == true` и `EXECUTOR_GROUP_CHAT_ID` пустой ИЛИ совпадает с текущим. Иначе отказ.
   - Создание топиков через 4 × `cmd.tg.create_forum_topic` (ждём 4 × `events.tg.topic_created` по `correlation_id`).
   - `cmd.tg.send_message` с готовым блоком env-переменных.
2. На старте core: если `EXECUTOR_GROUP_CHAT_ID` задан, проверить через Bot API (через gateway-tg) что бот в чате админ и топики существуют. Если что-то не так — ERROR в `🤖 Логи`, **не падать**.

## Acceptance criteria

- [x] Команда `/setup_team_group` доступна только исполнителям из `executors.yaml`. От не-исполнителя — молча игнорируется. *(проверка `actor is None or not actor.is_active` в [tg_message handler](../services/core/src/core/handlers/tg_message.py) `_handle_setup_team_group`)*
- [x] Если `EXECUTOR_GROUP_CHAT_ID` уже задан и не совпадает с `chat_id` текущей группы — бот пишет отказ с пояснением, не создаёт топики. *(test_chat_id_mismatch)*
- [x] Если совпадает или пуст — бот создаёт топики `🆕 Входящие`, `🚨 Эскалации`, `📊 Сводка`, `🤖 Логи` (General не трогаем). *(test_happy_path_emits_4_creates)*
- [x] В чат выводится готовый блок env-переменных. *(`TeamGroupEnvBlock.render()` + test_all_creates_emit_env_block)*
- [x] Команда `/print_topic_id` работает в любом топике и печатает `message_thread_id`. *(test_returns_topic_id + test_in_general_returns_no_thread_id)*
- [x] На старте `core` валидирует все `EXECUTOR_GROUP_*` если они заданы: если что-то не сходится — ERROR в логи + сервис продолжает работать. *([core/main.py](../services/core/src/core/main.py) — `executor_group_topic_env_missing` log; не падает)*

**Артефакты текущего шага:**
- Миграция [0005_team_group_topic_setup.py](../services/core/migrations/versions/20260521_0005_team_group_topic_setup.py) — таблица `team_group_topic_setup` (одна строка на топик с UNIQUE correlation_id и UNIQUE(chat_id, role))
- Domain [team_group.py](../services/core/src/core/domain/team_group.py): `TeamTopicRole`, `TOPIC_NAMES`, `TeamGroupEnvBlock.render()`, тексты
- Use-case'ы [setup_team_group.py](../services/core/src/core/services/setup_team_group.py): `SetupTeamGroup` (phase 1), `AttachTeamTopic` (phase 2 — собирает 4 ответа от gateway-tg и публикует env-блок когда все пришли), `PrintTopicId`
- multiplexer в [tg_topic_created handler](../services/core/src/core/handlers/tg_topic_created.py) — теперь различает ticket и team-group setup по correlation_id
- ветки `/setup_team_group` и `/print_topic_id` в [tg_message handler](../services/core/src/core/handlers/tg_message.py)
- 10 новых integration-тестов, всего 116 passing

## Architecture note

Команда `/setup_team_group` идёт через те же два этапа, что и создание тикета (spec 002): сначала core эмитит 4× `cmd.tg.create_forum_topic` с UNIQUE correlation_id, далее ждёт 4 ответа `events.tg.topic_created`. Когда все 4 строки в `team_group_topic_setup` получили `topic_id`, бот публикует env-блок и помечает их `finished_at`. Заказчик копирует блок в `.env`, перезапускает стек.

Эскалации (`🚨 Эскалации`) создаются как топик, но **не** попадают в env — в v1 они зарезервированы под будущую логику (SPEC §3.2), поэтому в env уезжают только три топика: incoming/digest/logs.

## Data changes

Нет в БД. Изменения в `.env`.

## Out of scope

- Множественные командные группы (предполагается ровно одна)
- Интерактивный менеджмент executors без рестарта (SPEC §18.4) — пока через YAML + `/reload_executors` из admin-команд (§3.7)

## Open questions

- Что делать, если бот не смог создать какой-то из топиков (превышен лимит)? — Отказ всей операции, удаление уже созданных через `deleteForumTopic` (если получится), сообщение об ошибке. **Решить** до начала имплементации.
