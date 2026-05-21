# Smoke-чеклист v1

Ручной прогон полного цикла против реального тестового бота. После всех 9 шагов SDD у нас должны работать: onboarding группы, создание тикета, назначение, закрытие, ежедневный дайджест, синхронизация в Sheets.

## Подготовка (одноразовая)

### 1. Бот в Telegram

1. Создать тестового бота в [@BotFather](https://t.me/BotFather): `/newbot`, скопировать токен.
2. **Отключить privacy mode** (обязательно, иначе бот не видит сообщений в группах):
   - `/setprivacy` → выбрать бота → `Disable`
3. Опционально — выбрать иконки топиков:
   ```python
   # быстрый скрипт, один раз
   import asyncio
   from aiogram import Bot
   async def main():
       async with Bot("YOUR_TOKEN") as bot:
           stickers = await bot.get_forum_topic_icon_stickers()
           for s in stickers:
               print(s.custom_emoji_id, s.emoji)
   asyncio.run(main())
   ```
   Выбрать 3 ID: ⚪ (new), 🟡 (in_progress), ✅ (closed) — записать в `.env` как `TOPIC_ICON_NEW` / `TOPIC_ICON_IN_PROGRESS` / `TOPIC_ICON_CLOSED`. Если оставить пустыми — Telegram использует дефолтную иконку.

### 2. Группы

1. **Командная группа**: создать супергруппу `<Команда> — Backoffice`, включить `Manage group → Topics: ON`. Добавить бота админом со всеми правами. **Ничего вручную не создавать** — далее команда сделает топики сама.
2. **Группа заказчика**: то же самое, добавить бота админом. Заказчика добавить как member'а (без admin-прав).

### 3. `executors.yaml`

Прописать админов в `config/executors.yaml`:

```yaml
executors:
  - username: ivan_petrov  # без @
    full_name: Иван Петров
    is_lead: true
```

### 4. `.env`

```
BOT_TOKEN=<from BotFather>
BOT_USE_WEBHOOK=false   # для smoke — long-poll
POSTGRES_DSN=postgresql+asyncpg://tickets:tickets@postgres:5432/tickets
REDIS_URL=redis://redis:6379
EXECUTORS_CONFIG_PATH=/app/config/executors.yaml
DIGEST_CRON=0 9 * * *
TZ=Europe/Moscow

# Опционально — Sheets:
# GOOGLE_SHEETS_ID=<sheet id>
# GOOGLE_SHEETS_CREDENTIALS_JSON=<json одной строкой>

# Иконки можно оставить пустыми
TOPIC_ICON_NEW=
TOPIC_ICON_IN_PROGRESS=
TOPIC_ICON_CLOSED=

# EXECUTOR_GROUP_* — заполнятся после /setup_team_group
EXECUTOR_GROUP_CHAT_ID=
EXECUTOR_GROUP_TOPIC_INCOMING=
EXECUTOR_GROUP_TOPIC_DIGEST=
EXECUTOR_GROUP_TOPIC_LOGS=
```

### 5. Запуск стека

```bash
docker compose up -d --build
docker compose exec core alembic upgrade head
docker compose exec notifications alembic upgrade head  # опционально, notifications-сервис сейчас простаивает
docker compose exec sheets-sync alembic upgrade head
docker compose logs -f core gateway-tg
```

## Прогон сценария

### Шаг 1 — `/setup_team_group` в командной группе

Исполнитель (Иван) пишет `/setup_team_group` в General командной группы.

**Ожидаемо**:
- Бот создаёт 4 топика: `🆕 Входящие`, `🚨 Эскалации`, `📊 Сводка`, `🤖 Логи`
- В чат прилетает блок env-переменных

**Действие**: скопировать блок в `.env`, `docker compose up -d --force-recreate core gateway-tg sheets-sync scheduler notifications`.

> ⚠️ `docker compose restart` **не** перечитывает `.env`. Только `up -d --force-recreate` (или `down` + `up`) перечитает env-файл.

### Шаг 2 — Подключить группу заказчика через `/setup`

После добавления бота админом в группу заказчика Telegram пришлёт `my_chat_member`. Бот ответит **подсказкой**: «Я добавлен. Используйте `/setup` для группы заказчика, `/setup_team_group` для командной». Реальный onboarding запускается явной командой.

> ⚠️ **Important**: в `Manage group → Administrators → бот` обязательно включить toggle **`Manage topics`** (отдельный, обычно выключен по умолчанию). Без него `createForumTopic` упадёт с `Bad Request: not enough rights to create a topic`, и тикет создать не получится. `Delete Messages` и `Pin Messages` тоже нужны.

Исполнитель пишет в General группы заказчика **`/setup`**.

**Ожидаемо**:
- General переименован в `📋 Меню`
- В General запинено сообщение бота с 3 кнопками: `🆕 Новый тикет`, `📋 Мои тикеты`, `❓ Помощь`
- General закрыт (писать в нём могут только админы)
- В БД появилась запись в `customers` с заполненным `menu_message_id`

> 💡 Чтобы бот считал тебя исполнителем, нужно чтобы твой `telegram_user_id` был зарезолвлен в таблице `executors`. Резолв происходит автоматически при первом сообщении исполнителя в **командной группе** (которая указана в `EXECUTOR_GROUP_CHAT_ID`). Если командной группы ещё нет — можно прописать вручную: `UPDATE executors SET telegram_user_id = <id> WHERE username = '<username>';` Узнать свой numeric ID — через `@userinfobot`.

### Шаг 3 — Создать тикет (от заказчика)

Заказчик в General жмёт `🆕 Новый тикет`.

**Ожидаемо**:
- Меню сменяется на «Опишите задачу одним сообщением» + кнопка `❌ Отмена`
- General временно открыт

Заказчик пишет одно сообщение: `Поправить шапку\nКонкретно на лендинге A/B-теста`

**Ожидаемо** (всё в течение 1–2 секунд):
- Сообщение заказчика удалено
- Создан тикетный топик `#1 Поправить шапку` с иконкой ⚪
- В нём — запиненная шапка с описанием + кнопка `✅ Закрыть тикет`
- General снова закрыт
- Меню вернулось в `main`, на короткое время — «✅ Тикет #1 создан»
- В командной группе, топик `🆕 Входящие` — карточка с кнопками-именами исполнителей и URL «🔗 Открыть тикет»

### Шаг 4 — Назначить исполнителя

В `🆕 Входящие` Иван жмёт на кнопку `Иван Петров` (self-pickup).

**Ожидаемо**:
- Кнопки имён пропадают, остаётся «✅ Взят: Иван Петров» и URL-кнопка
- Иконка тикетного топика меняется на 🟡
- Шапка тикета обновляется: «🟡 В работе», «Исполнитель: Команда поддержки» (заказчик не видит имени)

### Шаг 5 — Закрыть тикет (от заказчика)

Заказчик в тикетном топике жмёт `✅ Закрыть тикет`.

**Ожидаемо**:
- Шапка меняется на «❓ Закрыть тикет? [Да, закрыть] [Отмена]»
- Жмёт `Да, закрыть`:
  - Имя топика → `[✅] #1 Поправить шапку`, иконка ✅
  - Финальное сообщение «✅ Тикет закрыт. Спасибо!»
  - Шапка обновляется в финальное состояние (без кнопок)
  - Топик закрывается (писать нельзя)

Проверка прав: если **другой member группы** жмёт `Закрыть` — должен получить toast «Закрыть тикет может только заказчик».

### Шаг 6 — Admin-команды

- `/list_customers` от Ивана → бот отвечает списком с маркерами активности
- `/rename_customer <chat_id> "Новое имя"` → бот переименовывает
- `/deactivate_customer <chat_id>` → бот деактивирует. Если заказчик жмёт меню — toast «Группа отключена»; новые тикеты не создаются
- `/activate_customer <chat_id>` → обратно
- `/reload_executors` → бот перечитывает yaml
- Любая admin-команда от **не-исполнителя** → молча игнорируется

### Шаг 7 — Daily digest

Подкрутить `DIGEST_CRON=*/2 * * * *` (раз в 2 минуты), перезапустить scheduler.

**Ожидаемо**: через 2 минуты в `📊 Сводка` появляется сообщение с тремя счётчиками (Новых / В работе / Закрытых).

Откатить cron на `0 9 * * *` после проверки.

### Шаг 8 — sheets-sync (если настроен)

Открыть Google Sheets, лист `Tickets`. На каждом событии (created / assigned / closed) должна обновляться соответствующая строка.

## Что проверить в логах

```bash
docker compose logs -f core | jq -r '.event // .'
docker compose logs -f gateway-tg
docker compose logs -f sheets-sync
```

Не должно быть `ERROR` уровня. `WARNING` допустим только для:
- `executor_group_topic_env_missing` (до выполнения шага 1)
- `bot_kicked_from_customer_group` (если кикнули бота — это норма)
- `incoming_card_no_active_executors` (если в YAML никого нет с резолвнутым `telegram_user_id`)

## Откат / переустановка

```bash
docker compose down -v        # сносим volumes — БД и Redis-данные
docker compose up -d --build  # с нуля
```
