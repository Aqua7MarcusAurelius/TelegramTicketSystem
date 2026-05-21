# 009. sheets-sync + scheduler + daily digest + smoke

**Status:** in-review (всё реализовано; e2e smoke — отдельный руководящий чек-лист)
**Author:** team
**Created:** 2026-05-21

## Why

После шагов 1–8 у нас работает полная цепочка от Telegram до core. Остаётся:

1. **sheets-sync** — read-only зеркало в Google Sheets для аналитики (SPEC §12).
2. **scheduler** — APScheduler с ежедневным cron'ом для дайджеста (SPEC §11.4).
3. **Daily digest handler** в core — собирает сводку и шлёт в `📊 Сводка` (SPEC §11.2, §11.4, открытый вопрос §18.3).
4. **Smoke-чеклист** для ручного e2e против реального бота.

Это последний шаг до v1.

## Technical flow

### Денормализация в TicketCreated / TicketAssigned

Sheets-sync (и digest) — внешние потребители: им нужно `customer_title` и `assignee_full_name`, которых не было в схеме событий. По SPEC §10.1 запретно читать чужие таблицы, поэтому добавили оптимальные denormalized-поля в события — с `default ""` для обратной совместимости.

### sheets-sync

Структура (5 файлов в [services/sheets-sync/](../services/sheets-sync/)):
- Миграция `0001_initial_sheets_sync` — создаёт `sheets_sync_state` (маппинг ticket_id → row номер) и `sheets_sync_processed_events`. У каждого сервиса теперь свой `alembic_version_*` table, фильтр `include_object` для не-своих таблиц.
- `repository/{base.py, models.py, state.py}` — SQLAlchemy Base, модели, репозиторий + processed_events helper
- `sheets_client.py` — gspread обёртка с `asyncio.to_thread`; ищет колонки по заголовкам первой строки (`Tickets` лист, заголовки `ID/Заказчик/Название/Статус/...` — см. SPEC §12.2); `append_or_update` принимает existing row номер или None
- `services/sync_ticket.py` — три use-case'а (`PlanTicketCreated`, `PlanTicketAssigned`, `PlanTicketClosed`), возвращают `SyncPlan(row, existing_row_number)` или `Skipped`. In-memory кеш денормализованных полей тикета — чтобы при assigned/closed восстановить customer_title и т.п.
- `handlers/ticket_events.py` — три FastStream subscriber'а складывают события в `asyncio.Queue`, **single-queue worker** обрабатывает по одному и пишет в Sheets через клиент (SPEC §11.5: однопоточный writer избегает race в gspread). Если `GOOGLE_SHEETS_CREDENTIALS_JSON` пуст — сервис работает «всухую» (только БД), это нужно для smoke без Google API.

### scheduler

`main.py`:
- `AsyncIOScheduler` с `SQLAlchemyJobStore(url=sync_dsn)` поверх Postgres
- Один job `daily_digest` по `DIGEST_CRON` (из env, default `0 9 * * *`)
- Job публикует `DailyDigestTick` через FastStream broker

`_sync_dsn` конвертит `postgresql+asyncpg://...` → `postgresql+psycopg2://...` (APScheduler синхронен). `psycopg2-binary` уже в deps scheduler-сервиса.

### Daily digest в core

`services/daily_digest.py`:
- `BuildDailyDigest` принимает `DailyDigestTick`, считает три счётчика через одно групповое `SELECT status, count(*) FROM tickets GROUP BY status`
- Возвращает `DigestResult` с одной `CmdSendMessage` в `EXECUTOR_GROUP_TOPIC_DIGEST`
- Идемпотентность через `processed_events`

`handlers/daily_digest.py` — стандартная FastStream-обёртка; зарегистрирована в `core.main`.

### Smoke

[docs/SMOKE.md](../docs/SMOKE.md) — пошаговый ручной чек-лист: настройка бота через @BotFather, executors.yaml, `.env`, поднятие стека, `/setup_team_group` → onboarding группы заказчика → создание тикета → assign → close → admin-команды → daily digest → проверка Sheets. Включает раздел про логи и команды отката.

## Acceptance criteria

- [x] `TicketCreated` несёт `customer_title`, `TicketAssigned` — `assignee_full_name`. *([shared/events/ticket.py](../shared/src/shared/events/ticket.py); существующие тесты не сломаны)*
- [x] sheets-sync создаёт row при `events.ticket.created`, обновляет на `events.ticket.assigned` / `events.ticket.closed`. *(6 integration-тестов: test_first_seen_creates_plan_with_append, test_updates_existing_row, test_updates_to_closed + edge cases)*
- [x] Идемпотентность sheets-sync — отдельная `sheets_sync_processed_events`. *(test_idempotency_on_repeat)*
- [x] Без Google credentials sheets-sync поднимается и не падает. *(client=None путь в [handlers/ticket_events.py](../services/sheets-sync/src/sheets_sync/handlers/ticket_events.py))*
- [x] Однопоточный writer — все события идут через одну `asyncio.Queue`. *([handlers/ticket_events.py](../services/sheets-sync/src/sheets_sync/handlers/ticket_events.py))*
- [x] scheduler публикует `DailyDigestTick` по cron из env. *(unit-тест test_publish_daily_digest_tick_emits_event + проверка `_sync_dsn`)*
- [x] core слушает `events.schedule.daily_digest` и публикует `CmdSendMessage` в `EXECUTOR_GROUP_TOPIC_DIGEST`. *(3 integration-теста для `BuildDailyDigest`)*
- [x] Если командная группа не настроена — digest skip без падения. *(test_skipped_if_team_group_not_configured)*
- [x] Каждый сервис имеет свой `alembic_version_<name>` table; check на каждом сервисе чистый. *(env.py + include_object фильтр)*
- [x] [SMOKE.md](../docs/SMOKE.md) с пошаговым ручным чек-листом.

**Артефакты этого шага:**
- Миграция [0001_initial_sheets_sync.py](../services/sheets-sync/migrations/versions/20260521_0001_initial_sheets_sync.py)
- sheets-sync целиком собран: [sheets_client.py](../services/sheets-sync/src/sheets_sync/sheets_client.py), [services/sync_ticket.py](../services/sheets-sync/src/sheets_sync/services/sync_ticket.py), [handlers/ticket_events.py](../services/sheets-sync/src/sheets_sync/handlers/ticket_events.py)
- scheduler [main.py](../services/scheduler/src/scheduler/main.py)
- core [services/daily_digest.py](../services/core/src/core/services/daily_digest.py) + [handlers/daily_digest.py](../services/core/src/core/handlers/daily_digest.py)
- 13 новых тестов; всего **181 passing**
- Per-service `alembic_version_*` tables + `include_object` фильтр — заодно поправлено и для core/notifications

## Where v1 is now

С этим шагом проект закрывает все 4 первых initial-фичи + 2 onboarding-спеки + admin-команды + Telegram-обвязку (long-poll и webhook) + sheets-sync + scheduler.

Доменная часть: 100%. Telegram-сторона: 100%. Внешние интеграции: Google Sheets ✅, scheduler ✅, notifications-сервис как отдельный процесс — отложен (логика inbox-карточки и digest уже в core, по причинам §10.1; вынесем когда понадобятся кросс-сервисные нотификации).

Готово к ручному smoke против реального бота — см. [SMOKE.md](../docs/SMOKE.md).

## Out of scope

- Удаление notifications-сервиса как отдельного процесса. Сейчас он крутится пустой (subscriber'ов нет). Можно убрать из docker-compose, можно оставить как заглушку под будущие нужды (digest как отдельный сервис, эскалации §18.7).
- Более богатое содержимое digest (топ-исполнители, средний lead time, незакрытые > 7 дней) — открытый вопрос §18.3. Текущий минимум по принципу YAGNI.
- gspread-ошибки и retry-логика. Сейчас при падении gspread мы просто логируем и теряем событие. Production-grade — добавить retry с jitter поверх `asyncio.to_thread`.
