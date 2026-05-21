# Ticket Bot

Внутренняя тикет-система на базе Telegram-форумов. Заказчики работают в своих форум-группах, команда видит общую очередь во `🆕 Входящие` командной группы, метрики зеркалятся в Google Sheets.

Полная спецификация: [docs/SPEC.md](docs/SPEC.md).

## Архитектура

5 независимых сервисов, общающихся через Redis Streams (FastStream):

- `gateway-tg` — единственная точка контакта с Telegram Bot API
- `core` — бизнес-логика тикетов, FSM меню, владелец доменных таблиц
- `notifications` — маршрутизация уведомлений
- `scheduler` — APScheduler, ежедневные триггеры
- `sheets-sync` — зеркало в Google Sheets

Подробности по слоям — в [docs/SPEC.md §4](docs/SPEC.md).

## Стек

Python 3.12, aiogram 3.x, FastStream + Redis Streams, SQLAlchemy 2 (async) + asyncpg, Alembic, APScheduler, gspread, pydantic-settings, uv, ruff, mypy strict, pytest.

## Локальный запуск

```bash
# 1. Скопировать env-шаблон и заполнить (минимум BOT_TOKEN)
cp .env.example .env

# 2. Установить зависимости (uv workspace)
uv sync

# 3. Поднять стек
docker compose up --build

# 4. Применить миграции (каждый сервис свои)
docker compose exec core alembic upgrade head
docker compose exec notifications alembic upgrade head
docker compose exec sheets-sync alembic upgrade head
```

См. [docs/SPEC.md §15](docs/SPEC.md) для подробностей деплоя.

## SDD-workflow

Новые фичи начинаются со спеки в [specs/](specs/) по шаблону из [docs/SPEC.md §16](docs/SPEC.md). Сначала спека → ревью → failing-тесты → имплементация. Если по ходу реализации спека дрейфует, **сначала** правится спека, **потом** код.

Initial feature specs: [specs/001-customer-menu.md](specs/001-customer-menu.md) … [specs/006-onboard-team-group.md](specs/006-onboard-team-group.md).

## Структура

```
ticket-bot/
├── docs/SPEC.md           # источник правды
├── specs/                 # фичи по SDD-шаблону
├── shared/                # общие event-схемы, шина, db, logging
├── services/
│   ├── gateway-tg/
│   ├── core/
│   ├── notifications/
│   ├── scheduler/
│   └── sheets-sync/
├── config/executors.yaml  # список исполнителей (см. SPEC §3.4)
└── docker-compose.yml
```
