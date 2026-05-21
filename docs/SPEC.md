# Ticket Bot — Техническая спецификация

> Внутренняя система тикетов для команды на базе Telegram. Заказчики работают в своих форум-группах, команда видит общую очередь во `Входящих` командной группы, метрики зеркалятся в Google Sheets. Без ЛС с ботом — всё в групповых чатах.

---

## Содержание

1. [Назначение и границы](#1-назначение-и-границы)
2. [Роли](#2-роли)
3. [Telegram-структура](#3-telegram-структура)
4. [Архитектура](#4-архитектура)
5. [Технологический стек](#5-технологический-стек)
6. [Status machine](#6-status-machine)
7. [UX: флоу заказчика](#7-ux-флоу-заказчика)
8. [UX: флоу исполнителя](#8-ux-флоу-исполнителя)
9. [Шина событий](#9-шина-событий)
10. [Схема базы данных](#10-схема-базы-данных)
11. [Спецификации модулей](#11-спецификации-модулей)
12. [Google Sheets](#12-google-sheets)
13. [Конфигурация](#13-конфигурация)
14. [Структура проекта](#14-структура-проекта)
15. [Деплой](#15-деплой)
16. [SDD-workflow и шаблон спеки](#16-sdd-workflow-и-шаблон-спеки)
17. [Initial feature specs](#17-initial-feature-specs)
18. [Открытые вопросы](#18-открытые-вопросы)

---

## 1. Назначение и границы

### Что строим
Бот в Telegram, обслуживающий процесс «внутренний заказчик ↔ команда исполнителей». Заказчик создаёт тикеты, не выходя из своей форум-группы. Команда видит входящие, забирает в работу, ведёт обсуждение в выделенных топиках. Закрытие — только заказчиком. Аналитика — в Google Sheets.

### Что НЕ строим
- Веб-морду
- ЛС-интерфейс для заказчика (заказчик живёт строго в группе)
- Сложный workflow со статусами «в ревью», «заблокирован», «отменён» — статусов три (см. [§6](#6-status-machine))
- Реоупен закрытых тикетов — нужно новое = создаётся новый тикет
- Эскалации и SLA-триггеры в v1 (можно дописать позже без переделки шины)
- Файлы/вложения как first-class — пересылка через нативные средства Telegram внутри топика, без отдельной логики

---

## 2. Роли

| Роль | В Telegram | Что может |
|---|---|---|
| **Заказчик** | Member в одной из форум-групп заказчиков | Создавать и закрывать тикеты, видеть свои тикеты. С ботом в ЛС не общается. |
| **Исполнитель** | Анонимный админ в группах заказчиков, обычный участник командной группы | Брать тикеты (или быть назначенным коллегой), выполнять, общаться с заказчиком в тикетном топике. Закрывать тикеты не может. |
| **Тимлид** | То же что исполнитель | На уровне бота прав не имеет (v1). Если понадобится принудительное переназначение — добавим. |

Бот распознаёт **заказчика** в группе заказчика как любого пользователя, который не является админом этой группы (бот и исполнители — админы; заказчик — единственный member).

---

## 3. Telegram-структура

### 3.1 Группа заказчика
Один заказчик = одна **форум-группа** (`is_forum: true`).

Участники:
- 1 заказчик — member, без админ-прав
- N исполнителей — **анонимные админы** (`is_anonymous: true`)
- Бот — админ с правами: `can_manage_topics`, `can_delete_messages`, `can_pin_messages`, `can_post_messages` (если будет канал-режим), `can_restrict_members` (для возможного будущего)

Топики:
- **`General`** — переименован в `📋 Меню`. Постоянно **закрыт** (`closeGeneralForumTopic`). Внутри одно запиненное сообщение бота — главное меню (single-message UI, см. [§7.1](#71-главное-меню-в-general)).
- **Тикетные топики** — создаются ботом при создании тикета (`createForumTopic`). Открытые. Имя формата `#{id} {title}` для активных, `[✅] #{id} {title}` для закрытых. Иконка отражает статус (см. [§6](#6-status-machine)).

### 3.2 Командная группа
Одна форум-группа на всю команду. Приватная, без публичной ссылки.

Участники:
- Все исполнители (включая тимлида) — обычные участники или админы по орг-нуждам, на бот не влияет
- Бот — админ (те же права что выше)

Топики:
- **`🆕 Входящие`** — уведомления о новых тикетах с кнопками-именами исполнителей для назначения
- **`🚨 Эскалации`** — зарезервировано на будущее, в v1 пусто
- **`📊 Сводка`** — ежедневный дайджест от бота (раз в сутки)
- **`💬 Общее`** — обычный командный чат, бот не вмешивается
- **`🤖 Логи`** — служебные ошибки и предупреждения бота

### 3.3 ЛС с ботом

В v1 ЛС с ботом **не используется**. Если кто-либо пишет боту в личку — бот отвечает «Я работаю только в группах, обращайтесь в свою группу» и больше ничего не делает. Все взаимодействия — в групповых чатах.

### 3.4 Список исполнителей

Хранится в YAML-файле `config/executors.yaml`, который монтируется в контейнеры как volume:

```yaml
# config/executors.yaml
executors:
  - username: ivan_petrov
    full_name: Иван Петров
  - username: maria_s
    full_name: Мария Сидорова
  - username: petr_k
    full_name: Пётр Кузнецов
```

На старте `core` читает файл и upsert'ит записи в таблицу `executors`. `telegram_user_id` подтягивается так:
- Если уже известен по предыдущим взаимодействиям — берётся из БД
- Иначе — бот резолвит его при первом сообщении этого `@username` в командной группе и сохраняет
- До резолва исполнитель **не появляется кнопкой** в `Входящих` (в логи пишется WARNING)

**Добавить исполнителя:** дописать запись в `executors.yaml`, попросить нового исполнителя написать что угодно в командную группу (для резолва user_id), перезапустить `core` (`docker compose restart core`).

**Убрать исполнителя:** удалить запись из YAML, перезапустить `core`. Запись в БД помечается `is_active=false` (не удаляется — нужна для целостности FK на старых тикетах, где он был назначен).

В `executors` остаётся также флаг `is_lead` (можно проставить в YAML) — на будущее, прав в v1 не даёт.

### 3.5 Подключение новой группы заказчика

Группа создаётся **человеком** в Telegram (Bot API не умеет создавать чаты — это ограничение платформы). Бот автоматизирует настройку и регистрацию.

#### Предусловия

Ответственный сотрудник (любой из `executors.yaml`):

1. Создаёт супергруппу в режиме форума: `New group → after creating, Manage group → Topics: ON`
2. Называет группу по соглашению `<Имя заказчика> — Тикеты` (это имя бот использует как `customers.title`)
3. Приглашает заказчика как обычного member'а
4. Приглашает исполнителей и делает их **анонимными админами**: `Manage group → Administrators → Add → toggle "Hide identity"`
5. Добавляет **бота** как админа с правами: `Manage Topics`, `Delete Messages`, `Pin Messages`

#### Автоматическая регистрация

Когда бот получает админские права в новой группе, Telegram присылает ему `my_chat_member`. `gateway-tg` публикует на шину `events.tg.bot_membership_changed`. `core` обрабатывает:

1. **Проверка типа чата**: `chat_type == 'supergroup'` AND `is_forum == true`
   - Если нет — бот пишет в General: «Эта группа не в режиме форума. Включите форум-режим в настройках, затем нажмите /setup»
2. **Проверка прав бота**: все нужные галочки выставлены
   - Если чего-то не хватает — пишет конкретный чек-лист: «Не хватает: Manage Topics, Delete Messages. Дайте права и нажмите кнопку ниже» с кнопкой `🔄 Проверить ещё раз`
3. **Проверка дублей**: если `customers.telegram_chat_id` уже есть — выводит «Группа уже зарегистрирована за заказчиком '<имя>'» и выходит
4. **Если всё ок** — onboarding:
   - INSERT в `customers` (`title = chat.title`, `is_active = true`)
   - `cmd.tg.edit_general_forum_topic` → переименовать General в `📋 Меню`
   - `cmd.tg.send_message` в General — отправить главное меню (см. §7.1)
   - `cmd.tg.pin_message` — запинить это сообщение, записать `message_id` в `customers.menu_message_id`
   - `cmd.tg.close_general_forum_topic`
   - Чистка системных сообщений от своих действий (`forum_topic_closed`, и т.п.)
   - Финальное `cmd.tg.answer_callback_query` или toast «✅ Группа подключена, заказчик может создавать тикеты»

#### Ручной запуск (`/setup`)

Если автозапуск не сработал (например, бот был добавлен в группу до старта сервиса — `my_chat_member` улетел в пустоту), любой админ группы пишет в General `/setup`. Бот выполняет ровно ту же логику что и при автозапуске.

#### Если бота выгнали из группы

Бот получает `my_chat_member` с `new_status == 'left'` или `'kicked'`. `core`:
- Логирует WARNING в `🤖 Логи`
- **Не** помечает заказчика как неактивного автоматически (kick мог быть случайным). Решение принимает админ через `/deactivate_customer`.

### 3.6 Подключение командной группы (one-time)

Командная группа создаётся однократно при первом развёртывании и обычно не меняется.

#### Шаги

1. Создать форум-группу `<Имя команды> — Backoffice`, форум-режим включить
2. Добавить всех исполнителей (обычные участники, не анонимные)
3. Добавить бота как админа с теми же правами что для группы заказчика
4. Создать вручную топики (или см. ниже про `/setup_team_group`):
   - `🆕 Входящие`
   - `🚨 Эскалации` (зарезервирован)
   - `📊 Сводка`
   - `💬 Общее` — можно использовать дефолтный General
   - `🤖 Логи`
5. В каждом топике написать `/print_topic_id` — бот в ответ напишет `topic_id`. Скопировать в `.env`:
   - `EXECUTOR_GROUP_CHAT_ID`
   - `EXECUTOR_GROUP_TOPIC_INCOMING`
   - `EXECUTOR_GROUP_TOPIC_DIGEST`
   - `EXECUTOR_GROUP_TOPIC_LOGS`
6. `docker compose restart` чтобы сервисы подхватили env

#### Альтернатива: `/setup_team_group`

В новой форум-группе любой админ из `executors.yaml` пишет `/setup_team_group`. Бот:
- Проверяет: ещё нет настроенной командной группы (по env или по специальной записи в БД)
- Создаёт топики автоматически (`createForumTopic` ×4)
- В ответ выводит готовый блок текста для `.env`:
  ```
  EXECUTOR_GROUP_CHAT_ID=-1001234567890
  EXECUTOR_GROUP_TOPIC_INCOMING=2
  EXECUTOR_GROUP_TOPIC_DIGEST=3
  EXECUTOR_GROUP_TOPIC_LOGS=4
  ```
- Просит админа закинуть это в `.env` и перезапустить сервисы

#### Валидация на старте

При запуске `core` проверяет:
- `EXECUTOR_GROUP_CHAT_ID` указан и бот в этом чате админ
- Все `EXECUTOR_GROUP_TOPIC_*` существуют (через `getForumTopic`)
- Если что-то не так — пишет ERROR в логи и **не падает**, продолжает обслуживать клиентские группы. Уведомления просто никуда не пойдут до починки env.

### 3.7 Admin-команды

В отличие от пользовательских кнопок (заказчик не пишет команды), есть набор обслуживающих команд для исполнителей. Доступны только пользователям из `executors.yaml`. Работают в **любом** чате с ботом, ответ приходит в тот же чат.

| Команда | Назначение |
|---|---|
| `/setup` | Повторный onboarding группы заказчика (см. §3.5). Работает в группе, где её ввели. |
| `/setup_team_group` | Onboarding командной группы (см. §3.6). |
| `/rename_customer <chat_id> "имя"` | Обновляет `customers.title`. Можно вызывать из любого чата. |
| `/deactivate_customer <chat_id>` | `customers.is_active = false`. Главное меню перестаёт работать, новые тикеты не создаются (кнопка отвечает toast «Группа отключена»), существующие тикеты можно закрывать. |
| `/activate_customer <chat_id>` | Откатывает `/deactivate_customer`. |
| `/print_topic_id` | Печатает `message_thread_id` текущего топика. Debug-утилита для настройки env. |
| `/reload_executors` | Перечитать `executors.yaml` без рестарта `core` (на случай добавления одного человека). |
| `/list_customers` | Список всех зарегистрированных групп с `chat_id`, `title`, `is_active`. |

Команды от не-исполнителей **молча игнорируются** (никакого toast'а, никакого ответа) — чтобы случайные люди не палили существование функций.

---

## 4. Архитектура

### 4.1 Сервисы
5 независимых сервисов, общающихся через Redis Streams:

| Сервис | Назначение |
|---|---|
| `gateway-tg` | Единая точка контакта с Telegram. Long-poll (dev) / webhook (prod). Превращает входящие Telegram-апдейты в события шины. Принимает команды шины и вызывает Bot API. |
| `core` | Бизнес-логика: тикеты, статусы, назначения, FSM single-message UI. Владелец доменных таблиц. |
| `notifications` | Маршрутизация уведомлений: какое событие → кому → каким сообщением → куда. |
| `scheduler` | APScheduler. Тайм-триггеры (ежедневный дайджест). |
| `sheets-sync` | Подписка на доменные события, обновление Google Sheets. |

### 4.2 Хранилища

**Postgres** — единая база, владение таблицами по конвенции (см. [§10.1](#101-владение-таблицами)).

**Redis** — три DB-нумера в одном инстансе:
- `db=0` — стримы FastStream
- `db=1` — FSM-состояния single-message UI (Pydantic-модели в JSON под ключами `fsm:{user_id}:{chat_id}`)
- `db=2` — кэш (на будущее: rate-limit, throttle)

### 4.3 Поток данных (пример)

```
Заказчик пишет в General  
        │  
        ▼  
[gateway-tg]  получает Update от Telegram  
        │  publishes events.tg.message  
        ▼  
[core]  читает событие, понимает что это ответ на ForceReply  
        ├─► пишет в БД: создаёт ticket  
        ├─► publishes cmd.tg.create_forum_topic  
        ├─► publishes cmd.tg.delete_message  (убирает сообщение заказчика)  
        ├─► publishes cmd.tg.close_general_forum_topic  
        ├─► publishes events.ticket.created  
        ▼  
[gateway-tg]  выполняет команды, отвечает событием events.tg.topic_created  
[notifications]  читает events.ticket.created → publishes cmd.tg.send_message в Входящие  
[sheets-sync]  читает events.ticket.created → добавляет строку в Sheets  
```

Все межсервисные взаимодействия — через шину. Прямых вызовов нет.

---

## 5. Технологический стек

| Слой | Технология | Версия |
|---|---|---|
| Язык | Python | 3.12 |
| Бот-фреймворк | aiogram | 3.x |
| Шина | FastStream + Redis Streams | latest |
| ORM | SQLAlchemy (async) | 2.x |
| Миграции | Alembic | latest |
| Драйвер БД | asyncpg | latest |
| Планировщик | APScheduler | 3.x |
| Sheets-клиент | gspread | latest |
| Конфиг | pydantic-settings | latest |
| Зависимости | uv | latest |
| Линт + форматтер | ruff | latest |
| Типы | mypy (strict) | latest |
| Тесты | pytest + pytest-asyncio | latest |
| Моки HTTP | respx | latest |
| Логи | structlog (JSON в prod, console в dev) | latest |
| Ошибки (опционально) | sentry-sdk | latest |
| Контейнеризация | Docker + docker-compose | — |

---

## 6. Status machine

### Состояния

| Код | Иконка топика | Семантика |
|---|---|---|
| `new` | ⚪ | Тикет создан, исполнитель не назначен |
| `in_progress` | 🟡 | Исполнитель определился, идёт работа |
| `closed` | ✅ | Заказчик закрыл (выполнено или отменено — не различаем) |

### Переходы

```
[new] ──(executor takes)──► [in_progress] ──(customer closes)──► [closed]
```

Других переходов нет:
- Откатиться из `in_progress` обратно в `new` нельзя (если исполнитель отказался — назначаем другого, статус не меняется, меняется только `assignee_id`)
- Реоупен закрытого тикета не поддерживается — заказчик создаёт новый

### Иконки топиков
Берутся из набора `getForumTopicIconStickers`. Маппинг конкретных `custom_emoji_id` определяется на старте — выбираются 3 иконки из доступного набора, их ID кладутся в конфиг как `TOPIC_ICON_NEW`, `TOPIC_ICON_IN_PROGRESS`, `TOPIC_ICON_CLOSED`.

### Имя топика
- `new` / `in_progress`: `#{id} {title}` (например, `#42 Поправить шапку`)
- `closed`: `[✅] #{id} {title}` (видно в списке топиков сразу что закрыт)

---

## 7. UX: флоу заказчика

### 7.1 Главное меню в General

Запиненное сообщение бота в `General`. Состояния single-message UI:

#### Состояние `main`
```
👋 Здесь вы можете создавать задачи для команды.

[🆕 Новый тикет]
[📋 Мои тикеты]
[❓ Помощь]
```

#### Состояние `creating_prompt` (после нажатия «🆕 Новый тикет»)
```
Опишите задачу одним сообщением 👇

[❌ Отмена]
```
Параллельно бот делает `reopenGeneralForumTopic`. После получения сообщения или таймаута (2 мин) — `closeGeneralForumTopic` и возврат в `main`.

#### Состояние `my_tickets` (после «📋 Мои тикеты»)
```
Ваши активные тикеты (3):

🟡 #42 Поправить шапку на лендинге
🟡 #41 Добавить экспорт в CSV
⚪ #38 Логи падают по ночам

[🗂 Закрытые] [⬅️ Назад]
```
Каждая строка — inline-кнопка с deep-link `https://t.me/c/{internal_chat_id}/{topic_id}`. При наличии большого количества — пагинация (кнопки `◀️` `▶️`, по 10 на страницу).

#### Состояние `closed_tickets`
Аналогично, но показывает тикеты в статусе `closed` за последние 30 дней. Кнопка `[⬅️ Назад]` возвращает в `my_tickets`.

#### Состояние `help`
Статичный текст с описанием процесса. Контент — TODO для заказчика бизнес-процесса. Кнопка `[⬅️ Назад]`.

### 7.2 Флоу «Создать тикет»

1. Заказчик жмёт `🆕 Новый тикет` в `General`
2. Бот получает callback, переходит FSM в `creating_prompt`
3. Бот publishes `cmd.tg.reopen_general_forum_topic`
4. Бот publishes `cmd.tg.edit_message_text` — обновляет меню на промпт
5. Заказчик пишет описание в General (`events.tg.message`)
6. `core` распознаёт: ожидаемый ответ от этого user_id, в этом chat_id, в состоянии `creating_prompt`
7. `core` создаёт запись `tickets` (status=`new`, assignee_id=NULL, description=text)
8. `core` publishes `cmd.tg.create_forum_topic` с именем `#{id} {title}`, icon=`TOPIC_ICON_NEW`
9. `gateway-tg` выполняет, отвечает `events.tg.topic_created` с `topic_id`
10. `core` пишет `tickets.topic_id`, publishes `cmd.tg.send_message` в новый топик — шапка тикета (см. [§7.3](#73-шапка-тикетного-топика))
11. `core` publishes `cmd.tg.pin_message` для шапки
12. `core` publishes `cmd.tg.delete_message` (сообщение заказчика в General)
13. `core` publishes `cmd.tg.close_general_forum_topic`
14. `core` чистит системные сообщения об open/close General (получает их через `events.tg.message`, фильтрует `is_service_message`)
15. `core` обновляет меню обратно в `main` с тостом «✅ Тикет #{id} создан»
16. `core` publishes `events.ticket.created` — дальше слушают `notifications` и `sheets-sync`

**Таймаут.** Если в `creating_prompt` 2 мин не приходит ответ:
- `core` сбрасывает FSM в `main`
- publishes `cmd.tg.close_general_forum_topic`
- publishes `cmd.tg.edit_message_text` — возврат меню с тостом «⏱ Время вышло»

**Заголовок vs описание.** В v1: первая строка ответа = заголовок, остальное = описание. Если строка одна — она и заголовок, описание пустое.

### 7.3 Шапка тикетного топика

Первое сообщение бота в новом топике, запинено:

```
📌 Тикет #42

Поправить шапку на лендинге

────
Статус: ⚪ Новый
Исполнитель: не назначен
Создан: 2026-05-21 14:32

[✅ Закрыть тикет]
```

Кнопка `Закрыть` видна всем, но callback обрабатывается только если `callback_query.from_user.id == ticket.created_by_user_id`. Иначе — `answerCallbackQuery` с тостом «Закрыть тикет может только заказчик».

Шапка обновляется ботом при изменении статуса/назначения (`cmd.tg.edit_message_text`).

### 7.4 Флоу «Закрыть тикет»

1. Заказчик в тикетном топике жмёт `✅ Закрыть тикет`
2. Бот проверяет `from_user.id == created_by_user_id`. Если нет — toast и выход.
3. Бот через `cmd.tg.edit_message_text` обновляет шапку: добавляет «Закрыть тикет? [Да, закрыть] [Отмена]»
4. Заказчик жмёт `Да`
5. `core`:
   - `tickets.status = closed`, `closed_at = now()`, `closed_by_user_id = from_user.id`
   - publishes `cmd.tg.edit_forum_topic` (имя `[✅] #{id} {title}`, icon=`TOPIC_ICON_CLOSED`)
   - publishes `cmd.tg.send_message` — финальное сообщение «Тикет закрыт. Спасибо!»
   - publishes `cmd.tg.close_forum_topic`
   - publishes `cmd.tg.edit_message_text` — шапка обновляется в финальное состояние (без кнопок)
   - publishes `events.ticket.closed`

После закрытия топик остаётся видимым (для истории), но писать в нём нельзя.

---

## 8. UX: флоу исполнителя

Вся работа исполнителей — в командной группе. ЛС с ботом не используется (см. [§3.3](#33-лс-с-ботом)).

### 8.1 Уведомление во Входящих

При `events.ticket.created` сервис `notifications` отправляет в командную группу, топик `🆕 Входящие`:

```
🆕 Новый тикет #42

Заказчик: Команда Маркетинга
Тема: Поправить шапку на лендинге

Кто берёт?

[Иван Петров] [Мария Сидорова] [Пётр Кузнецов]
[Аня Иванова] [Сергей Лебедев]
[🔗 Открыть тикет]
```

- Кнопки-имена — формируются динамически из активных исполнителей (`executors.is_active=true` AND `telegram_user_id IS NOT NULL`)
- Раскладка: по 3 кнопки в ряд, в порядке как в YAML
- `🔗 Открыть тикет` — URL-кнопка с deep-link `https://t.me/c/{internal_chat_id}/{topic_id}` на тикетный топик в группе заказчика
- `callback_data` для кнопок имён: `assign:{ticket_id}:{executor_user_id}`

### 8.2 Флоу назначения

Любой из исполнителей в `executors` (включая тимлида) может нажать **любую** кнопку с именем — назначить себя или коллегу. Эта симметрия сознательная: и self-pickup, и назначение от тимлида проходят через один интерфейс.

1. Исполнитель A жмёт кнопку с именем B (B может равняться A)
2. `core` проверяет:
   - `from_user.id` присутствует в активных исполнителях → иначе toast «Вы не в списке исполнителей» и выход
   - Тикет ещё не назначен → иначе toast «Уже взят: <имя>» и выход
3. `core`:
   - `tickets.assignee_id = B.id`, `tickets.status = 'in_progress'`, `tickets.in_progress_at = now()`
   - В `ticket_events` пишет запись `{event_type: 'assigned', actor_user_id: A.user_id, payload: {assignee_id: B.id}}` — для аудита «кто кого назначил»
4. `core` publishes:
   - `cmd.tg.edit_message_text` — уведомление во `Входящих` редактируется: кнопки имён исчезают, остаётся текст «✅ Взят: <имя B>» и кнопка `[🔗 Открыть тикет]`
   - `cmd.tg.edit_forum_topic` в группе заказчика — иконка топика → `TOPIC_ICON_IN_PROGRESS`
   - `cmd.tg.edit_message_text` для шапки тикетного топика — статус «🟡 В работе», исполнитель «Команда поддержки» (анонимность перед заказчиком сохраняется)
   - `events.ticket.assigned` с полями `assignee_user_id` и `assigned_by_user_id`

### 8.3 Отмена/переназначение

В v1 не поддерживается через UI. Если назначенный исполнитель не сможет — пишет в `💬 Общее` коллегам, переназначение делается вручную (через БД или будущую функцию). Не закладываем в v1 — кейс редкий.

### 8.4 «Свои» тикеты для исполнителя

В v1 отдельного личного представления нет — все назначения видны во `Входящих` (каждое уведомление содержит «✅ Взят: <имя>»). Это сознательное упрощение под «всё в группе, доступно всем».

Если в реальном использовании окажется, что листать `Входящие` неудобно — добавим топик `📌 В работе` с автообновляемым ботом списком текущих активных тикетов и их исполнителей. Без ЛС.

---

## 9. Шина событий

### 9.1 Соглашения

- **Namespace**: `events.*` для фактов, `cmd.*` для команд.
- **Стримы Redis**: имя = namespace, например `events.ticket.created`, `cmd.tg.send_message`. По одному consumer-group на сервис.
- **Каждое сообщение содержит** обязательные поля:
  - `event_id: UUID` — уникальный, для идемпотентности
  - `event_version: int` — версия схемы, начиная с 1
  - `occurred_at: datetime` — момент возникновения в источнике (ISO 8601 UTC)
- **Идемпотентность**: каждый сервис ведёт таблицу `processed_events(event_id, processed_at)`. Перед обработкой проверяет, не видел ли уже.
- **Версионирование**: новые поля — `Optional`. Ломающие изменения — новый namespace `events.ticket.created.v2`, старые подписчики переписываются по плану.
- **Команды** — fire-and-forget. Если нужен ответ (например, `create_forum_topic` возвращает `topic_id`), отправитель ждёт соответствующее событие (`events.tg.topic_created` с тем же `correlation_id`).

### 9.2 Базовая схема события

```python
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime

class Event(BaseModel):
    event_id: UUID
    event_version: int = 1
    occurred_at: datetime
    correlation_id: UUID | None = None  # для связки команды и ответного события
```

Все события наследуются от `Event`.

### 9.3 События домена

```python
# events.ticket.created
class TicketCreated(Event):
    ticket_id: int
    customer_id: int
    customer_chat_id: int
    topic_id: int
    title: str
    description: str
    created_by_user_id: int
    created_at: datetime

# events.ticket.assigned
class TicketAssigned(Event):
    ticket_id: int
    assignee_user_id: int
    assigned_by_user_id: int  # кто нажал кнопку, может равняться assignee при self-pickup
    assigned_at: datetime

# events.ticket.closed
class TicketClosed(Event):
    ticket_id: int
    closed_by_user_id: int
    closed_at: datetime
```

### 9.4 События от gateway-tg (входящие Telegram-апдейты)

```python
from typing import Literal

# events.tg.message
class TgMessage(Event):
    chat_id: int
    chat_type: Literal["private", "group", "supergroup"]
    is_forum: bool
    topic_id: int | None
    user_id: int
    username: str | None
    full_name: str
    is_anonymous_admin: bool  # сообщение от анонимного админа группы
    is_bot: bool
    is_service_message: bool  # системные «топик закрыт» и т.п.
    service_message_type: str | None  # forum_topic_closed, forum_topic_reopened, ...
    text: str | None
    message_id: int
    reply_to_message_id: int | None

# events.tg.callback
class TgCallback(Event):
    chat_id: int
    chat_type: Literal["private", "group", "supergroup"]
    topic_id: int | None
    user_id: int
    message_id: int
    callback_data: str
    callback_query_id: str  # для answerCallbackQuery

# events.tg.topic_created (ответ на cmd.tg.create_forum_topic)
class TgTopicCreated(Event):
    chat_id: int
    topic_id: int
    name: str
    # correlation_id ссылается на команду create_forum_topic

# events.tg.bot_membership_changed
# Бот был добавлен/удалён/повышен в чате (Telegram my_chat_member update).
# Триггер onboarding-флоу (§3.5).
class TgBotMembershipChanged(Event):
    chat_id: int
    chat_type: Literal["private", "group", "supergroup"]
    chat_title: str | None
    is_forum: bool
    old_status: Literal["creator", "administrator", "member", "restricted", "left", "kicked"]
    new_status: Literal["creator", "administrator", "member", "restricted", "left", "kicked"]
    can_manage_topics: bool
    can_delete_messages: bool
    can_pin_messages: bool
    actor_user_id: int  # кто изменил статус (пригласил/выгнал/повысил)
```

### 9.5 Команды к gateway-tg

```python
# cmd.tg.send_message
class CmdSendMessage(Event):
    chat_id: int
    topic_id: int | None = None
    text: str
    reply_markup: dict | None = None
    parse_mode: Literal["HTML", "MarkdownV2"] | None = "HTML"
    disable_notification: bool = False

# cmd.tg.edit_message_text
class CmdEditMessageText(Event):
    chat_id: int
    message_id: int
    text: str
    reply_markup: dict | None = None
    parse_mode: Literal["HTML", "MarkdownV2"] | None = "HTML"

# cmd.tg.delete_message
class CmdDeleteMessage(Event):
    chat_id: int
    message_id: int

# cmd.tg.answer_callback_query
class CmdAnswerCallbackQuery(Event):
    callback_query_id: str
    text: str | None = None
    show_alert: bool = False

# cmd.tg.create_forum_topic
class CmdCreateForumTopic(Event):
    chat_id: int
    name: str
    icon_custom_emoji_id: str | None = None

# cmd.tg.edit_forum_topic
class CmdEditForumTopic(Event):
    chat_id: int
    topic_id: int
    name: str | None = None
    icon_custom_emoji_id: str | None = None

# cmd.tg.edit_general_forum_topic — для переименования General в «📋 Меню» при onboarding'е
class CmdEditGeneralForumTopic(Event):
    chat_id: int
    name: str

# cmd.tg.close_forum_topic
class CmdCloseForumTopic(Event):
    chat_id: int
    topic_id: int

# cmd.tg.reopen_forum_topic
class CmdReopenForumTopic(Event):
    chat_id: int
    topic_id: int

# cmd.tg.close_general_forum_topic
class CmdCloseGeneralForumTopic(Event):
    chat_id: int

# cmd.tg.reopen_general_forum_topic
class CmdReopenGeneralForumTopic(Event):
    chat_id: int

# cmd.tg.pin_message
class CmdPinMessage(Event):
    chat_id: int
    message_id: int
    disable_notification: bool = True
```

### 9.6 События планировщика

```python
# events.schedule.daily_digest
class DailyDigestTick(Event):
    pass
```

---

## 10. Схема базы данных

### 10.1 Владение таблицами

| Сервис | Таблицы |
|---|---|
| `core` | `customers`, `executors`, `tickets`, `ticket_events`, `fsm_state` |
| `notifications` | `notification_log` |
| `scheduler` | `apscheduler_jobs` (управляется APScheduler автоматически) |
| `sheets-sync` | `sheets_sync_state` |
| (каждый сервис) | `processed_events` (своя таблица в своём схема-неймспейсе, например `core.processed_events`) |

Дисциплина: чужие таблицы — не трогаем, в SQLAlchemy-моделях сервиса описаны только свои. Чтение чужих данных — через события шины, не SQL.

### 10.2 DDL

```sql
-- =========================================================
-- core
-- =========================================================

CREATE TABLE customers (
    id              SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    menu_message_id INT,                  -- ID запиненного главного меню в General
    onboarded_at    TIMESTAMPTZ,          -- когда успешно прошёл онбординг
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE executors (
    id                SERIAL PRIMARY KEY,
    telegram_user_id  BIGINT NOT NULL UNIQUE,
    username          TEXT,
    full_name         TEXT NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    is_lead           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TYPE ticket_status AS ENUM ('new', 'in_progress', 'closed');

CREATE TABLE tickets (
    id                  SERIAL PRIMARY KEY,
    customer_id         INT NOT NULL REFERENCES customers(id),
    topic_id            INT NOT NULL,           -- message_thread_id в Telegram
    header_message_id   INT,                    -- ID запиненной шапки в топике
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    status              ticket_status NOT NULL DEFAULT 'new',
    assignee_id         INT REFERENCES executors(id),
    created_by_user_id  BIGINT NOT NULL,        -- telegram_user_id заказчика
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    in_progress_at      TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    closed_by_user_id   BIGINT,
    UNIQUE (customer_id, topic_id)
);

CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_assignee ON tickets(assignee_id) WHERE status != 'closed';
CREATE INDEX idx_tickets_customer ON tickets(customer_id);

-- Лог переходов и значимых событий по тикету
CREATE TABLE ticket_events (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   INT NOT NULL REFERENCES tickets(id),
    event_type  TEXT NOT NULL,         -- 'created', 'assigned', 'closed', ...
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_user_id BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ticket_events_ticket ON ticket_events(ticket_id);

-- FSM single-message UI. Ключ — пара (user_id, chat_id).
-- Состояние и его данные — JSONB.
CREATE TABLE fsm_state (
    user_id     BIGINT NOT NULL,
    chat_id     BIGINT NOT NULL,
    state       TEXT NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,           -- для авто-таймаутов
    PRIMARY KEY (user_id, chat_id)
);

CREATE INDEX idx_fsm_expires ON fsm_state(expires_at) WHERE expires_at IS NOT NULL;

-- =========================================================
-- notifications
-- =========================================================

CREATE TABLE notification_log (
    id          BIGSERIAL PRIMARY KEY,
    event_id    UUID NOT NULL,           -- из исходного события шины
    kind        TEXT NOT NULL,           -- 'ticket_created', 'ticket_assigned', ...
    target_chat_id BIGINT NOT NULL,
    target_topic_id INT,
    message_id  INT,                     -- если сохраняем для последующего edit
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL,           -- 'sent', 'failed'
    error       TEXT
);

CREATE INDEX idx_notification_event ON notification_log(event_id);

-- =========================================================
-- sheets-sync
-- =========================================================

CREATE TABLE sheets_sync_state (
    ticket_id       INT PRIMARY KEY,
    sheet_row       INT NOT NULL,        -- номер строки в Sheets
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_event_id   UUID NOT NULL
);

-- =========================================================
-- Идемпотентность (один экземпляр на каждый сервис)
-- =========================================================

-- Для core
CREATE TABLE core_processed_events (
    event_id     UUID PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Аналогично для notifications_processed_events, sheets_sync_processed_events, etc.
-- Создаются в миграциях своего сервиса.
```

### 10.3 Миграции

Alembic, одна общая `migrations/` директория, но **подпапки по сервису**:

```
migrations/
├── env.py
├── alembic.ini
└── versions/
    ├── core/
    ├── notifications/
    └── sheets_sync/
```

Сервис при старте применяет миграции **только из своей подпапки**. Это даёт независимый цикл деплоя при сохранении одной БД.

---

## 11. Спецификации модулей

Для каждого модуля: что он слушает, что публикует, какие у него собственные обязанности.

### 11.1 `gateway-tg`

**Назначение.** Единственный модуль, который говорит с Telegram. Без бизнес-логики, тупой пайп.

**Подписан на (commands):**
- `cmd.tg.send_message`
- `cmd.tg.edit_message_text`
- `cmd.tg.delete_message`
- `cmd.tg.answer_callback_query`
- `cmd.tg.create_forum_topic`
- `cmd.tg.edit_forum_topic`
- `cmd.tg.close_forum_topic` / `cmd.tg.reopen_forum_topic`
- `cmd.tg.close_general_forum_topic` / `cmd.tg.reopen_general_forum_topic`
- `cmd.tg.pin_message`

**Публикует (events):**
- `events.tg.message`
- `events.tg.callback`
- `events.tg.topic_created` (ответ на `cmd.tg.create_forum_topic` с `correlation_id`)
- `events.tg.error` (опционально, если команда упала)

**Особенности:**
- При старте устанавливает webhook (prod) или запускает polling (dev).
- Privacy mode у бота должен быть выключен через @BotFather (`/setprivacy → Disable`), иначе bot не видит обычные сообщения в группах.
- Конвертация: anonymous admin определяется по `message.sender_chat == message.chat` (Telegram-специфика).
- Команды Bot API возвращают результат — gateway-tg трансформирует его в соответствующее event для отправителя.
- `my_chat_member` update (изменение статуса бота в чате) → публикуется как `events.tg.bot_membership_changed` для онбординга (см. §3.5).

### 11.2 `core`

**Назначение.** Бизнес-логика тикетов и UI-состояний. Владелец доменных таблиц.

**Подписан на:**
- `events.tg.message` — для FSM-переходов, захвата ответов на ForceReply, и обработки admin-команд (`/setup`, `/print_topic_id`, etc.)
- `events.tg.callback` — для нажатий кнопок
- `events.tg.topic_created` — для записи `tickets.topic_id` после `cmd.tg.create_forum_topic`
- `events.tg.bot_membership_changed` — для онбординга групп заказчиков (см. §3.5)
- `events.schedule.daily_digest` — формирует и публикует сводку (через `cmd.tg.send_message`)

**Публикует:**
- Все `events.ticket.*`
- Все `cmd.tg.*` для управления сообщениями и топиками

**Внутренняя структура:**
- `domain/` — чистая логика (статусы, переходы, валидация). Без IO.
- `repository/` — SQLAlchemy-доступ к БД.
- `services/` — use-case'ы: `CreateTicket`, `AssignTicket`, `CloseTicket`, `RenderMenu`, `HandleCallback`.
- `fsm/` — single-message UI state machines (`MenuFSM`, `CreateTicketFSM`).

### 11.3 `notifications`

**Назначение.** Решает кому, куда, каким сообщением.

**Подписан на:**
- `events.ticket.created` → шлёт в командную группу, топик `Входящие`
- `events.ticket.assigned` → обновляет уведомление во `Входящих` (через `cmd.tg.edit_message_text`): убирает кнопки имён, добавляет «✅ Взят: <имя>»
- `events.ticket.closed` → опционально шлёт в `Сводку` в конце дня (агрегирует)

**Публикует:**
- `cmd.tg.send_message`
- `cmd.tg.edit_message_text`

**Конфиг маршрутизации.** Лежит в коде (Python-словарь / YAML), не в БД. Меняется деплоем.

### 11.4 `scheduler`

**Назначение.** Тайм-события.

**APScheduler-задачи:**
- `daily_digest` — раз в день, 09:00 в локальной TZ — публикует `events.schedule.daily_digest`

**Job store:** Postgres (`apscheduler_jobs`), чтобы выживать рестарты.

### 11.5 `sheets-sync`

**Назначение.** Зеркало тикетов в Google Sheets.

**Подписан на:**
- `events.ticket.created` → append-row
- `events.ticket.assigned` → update assignee + in_progress_at
- `events.ticket.closed` → update closed_at + status

**Особенности:**
- Однопоточный writer (одна очередь, одна задача обрабатывает её последовательно), чтобы не было гонок при `find row by ticket_id`.
- Если Sheets API падает — событие остаётся в стриме до подтверждения, retry с экспоненциальной паузой.
- gspread синхронный — оборачивается в `asyncio.to_thread`.

---

## 12. Google Sheets

### 12.1 Принцип
БД — источник правды. Sheets — read-only зеркало. Обратной связи нет, ручные правки в Sheets бот не читает и не уважает.

### 12.2 Структура книги

#### Вкладка `Tickets` (основная)

| Колонка (заголовок 1-й строки) | Источник |
|---|---|
| `ID` | `tickets.id` |
| `Заказчик` | `customers.title` |
| `Название` | `tickets.title` |
| `Статус` | `tickets.status` (`new` / `in_progress` / `closed`) |
| `Исполнитель` | `executors.full_name` (или `@username`) |
| `Создан` | `tickets.created_at` |
| `В работе с` | `tickets.in_progress_at` |
| `Закрыт` | `tickets.closed_at` |
| `Lead time` | формула: `=Закрыт - Создан` (через `INDIRECT` или прямую ссылку) |
| `Ссылка` | формула HYPERLINK: `=HYPERLINK("https://t.me/c/...", "Открыть")` |

Бот ищет колонки **по заголовку первой строки**, не по букве. Это позволяет переставлять колонки.

#### Вкладка `By customer`
Сводная: за всё время + текущий месяц. Делается формулами `QUERY` / `COUNTIFS` / `AVERAGEIFS`. Бот не трогает.

#### Вкладка `By executor`
Аналогично, по исполнителям.

#### Вкладка `Monthly`
Pivot по месяцам.

### 12.3 Авторизация
- Service account, JSON-ключ в env-переменной `GOOGLE_SHEETS_CREDENTIALS_JSON` (или в файле и путь к нему — на выбор).
- ID таблицы в `GOOGLE_SHEETS_ID`.
- Sheet расшарен с email service-аккаунта с правами Editor.

---

## 13. Конфигурация

Все настройки — через env, через `pydantic-settings`.

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather |
| `BOT_USE_WEBHOOK` | `true` / `false` (dev = false → polling) |
| `BOT_WEBHOOK_URL` | Публичный URL для webhook (prod) |
| `BOT_WEBHOOK_SECRET` | Секрет в заголовке для проверки origin |
| `EXECUTOR_GROUP_CHAT_ID` | Chat ID командной группы |
| `EXECUTOR_GROUP_TOPIC_INCOMING` | Topic ID для `🆕 Входящие` |
| `EXECUTOR_GROUP_TOPIC_DIGEST` | Topic ID для `📊 Сводка` |
| `EXECUTOR_GROUP_TOPIC_LOGS` | Topic ID для `🤖 Логи` |
| `TOPIC_ICON_NEW` | `custom_emoji_id` для ⚪ |
| `TOPIC_ICON_IN_PROGRESS` | `custom_emoji_id` для 🟡 |
| `TOPIC_ICON_CLOSED` | `custom_emoji_id` для ✅ |
| `POSTGRES_DSN` | `postgresql+asyncpg://...` |
| `REDIS_URL` | `redis://...` (без указания db, добавляется в коде) |
| `EXECUTORS_CONFIG_PATH` | Путь к YAML-файлу с исполнителями, по умолчанию `/app/config/executors.yaml` |
| `GOOGLE_SHEETS_ID` | ID таблицы |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | JSON-ключ service account (целиком как строка) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `LOG_FORMAT` | `json` (prod) / `console` (dev) |
| `SENTRY_DSN` | опционально |
| `DIGEST_CRON` | Cron-выражение для дайджеста, по умолчанию `0 9 * * *` |
| `TZ` | Таймзона, например `Europe/Moscow` |

Чувствительные переменные (`BOT_TOKEN`, `GOOGLE_SHEETS_CREDENTIALS_JSON`, `SENTRY_DSN`, `POSTGRES_DSN`) — в `.env` (gitignored) для дева, в секрет-менеджере для прода.

---

## 14. Структура проекта

```
ticket-bot/
├── README.md
├── SPEC.md                    # этот документ
├── pyproject.toml             # корневой, общие dev-зависимости (ruff, mypy, pytest)
├── uv.lock
├── .env.example
├── docker-compose.yml
├── docker-compose.prod.yml
├── Makefile
├── alembic.ini
│
├── config/
│   └── executors.yaml         # список исполнителей, см. §3.4
│
├── shared/                    # общий код, импортируемый всеми сервисами
│   ├── pyproject.toml
│   └── src/shared/
│       ├── events/            # Pydantic-схемы всех событий шины
│       │   ├── __init__.py
│       │   ├── base.py        # class Event
│       │   ├── ticket.py
│       │   ├── tg.py
│       │   └── schedule.py
│       ├── bus/               # обёртка над FastStream (общая конфигурация)
│       ├── db/                # session factory, миксины
│       └── logging.py         # structlog setup
│
├── services/
│   ├── gateway-tg/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── src/gateway_tg/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── handlers/      # aiogram handlers (роутят апдейт → шина)
│   │       ├── executors/     # обработчики cmd.tg.* (исполняют Bot API)
│   │       └── config.py
│   │
│   ├── core/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── migrations/        # alembic-миграции только своих таблиц
│   │   └── src/core/
│   │       ├── main.py
│   │       ├── domain/        # чистая логика
│   │       │   ├── ticket.py
│   │       │   ├── status.py
│   │       │   └── menu.py
│   │       ├── repository/    # SQLAlchemy
│   │       ├── services/      # use-cases (CreateTicket, AssignTicket, ...)
│   │       ├── onboarding/    # §3.5–3.6: подключение групп заказчиков и командной
│   │       ├── admin/         # admin-команды §3.7
│   │       ├── fsm/           # single-message UI state machines
│   │       ├── handlers/      # подписчики на events
│   │       └── config.py
│   │
│   ├── notifications/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── migrations/
│   │   └── src/notifications/
│   │       ├── main.py
│   │       ├── routes.py      # карта event → message template
│   │       ├── templates/     # текстовые шаблоны
│   │       └── handlers/
│   │
│   ├── scheduler/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── src/scheduler/
│   │       ├── main.py
│   │       └── jobs/
│   │
│   └── sheets-sync/
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── migrations/
│       └── src/sheets_sync/
│           ├── main.py
│           ├── sheets_client.py
│           └── handlers/
│
├── specs/                     # SDD-спеки фич, см. §16
│   ├── 001-customer-menu.md
│   ├── 002-create-ticket.md
│   ├── 003-take-ticket.md
│   └── 004-close-ticket.md
│
├── tests/
│   ├── unit/                  # domain без IO, мокаем шину
│   ├── integration/           # с postgres+redis в docker
│   └── e2e/                   # бот целиком против моков Telegram API
│
└── docker/
    ├── postgres-init.sql
    └── ...
```

### 14.1 uv workspaces

В корневом `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["shared", "services/*"]
```

Сервисы импортируют `shared` через локальную зависимость:

```toml
# services/core/pyproject.toml
[project]
dependencies = [
    "shared",
    "aiogram>=3.0",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg",
    "faststream[redis]",
    ...
]

[tool.uv.sources]
shared = { workspace = true }
```

---

## 15. Деплой

### 15.1 Dockerfile (одинаковый шаблон для каждого сервиса)

```dockerfile
# services/<name>/Dockerfile
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared/ ./shared/
COPY services/<name>/ ./services/<name>/

RUN uv sync --frozen --no-dev --package <name>

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/<name>/src ./src
COPY --from=builder /app/shared/src ./shared_src

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/shared_src"

CMD ["python", "-m", "<name>.main"]
```

### 15.2 docker-compose.yml (dev)

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: tickets
      POSTGRES_USER: tickets
      POSTGRES_PASSWORD: tickets
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  gateway-tg:
    build:
      context: .
      dockerfile: services/gateway-tg/Dockerfile
    env_file: .env
    depends_on: [redis, postgres]

  core:
    build:
      context: .
      dockerfile: services/core/Dockerfile
    env_file: .env
    volumes:
      - ./config:/app/config:ro
    depends_on: [redis, postgres]

  notifications:
    build:
      context: .
      dockerfile: services/notifications/Dockerfile
    env_file: .env
    depends_on: [redis, postgres]

  scheduler:
    build:
      context: .
      dockerfile: services/scheduler/Dockerfile
    env_file: .env
    depends_on: [redis, postgres]

  sheets-sync:
    build:
      context: .
      dockerfile: services/sheets-sync/Dockerfile
    env_file: .env
    depends_on: [redis, postgres]

volumes:
  pg_data:
```

### 15.3 Запуск

```bash
# Создать .env из шаблона и заполнить
cp .env.example .env

# Поднять
docker compose up --build

# Применить миграции (каждый сервис свои)
docker compose exec core alembic upgrade head
docker compose exec notifications alembic upgrade head
docker compose exec sheets-sync alembic upgrade head

# Проверить логи
docker compose logs -f core
```

### 15.4 Production

- Managed Postgres (Neon / Supabase / RDS), `POSTGRES_DSN` указывает на него.
- Managed Redis (Upstash) с persistence — иначе при рестарте теряется поток событий из стримов.
- Каждый сервис как отдельный контейнер с auto-restart. На малых объёмах хватит одного VPS + docker compose. При росте — k8s/ECS.
- Webhook вместо polling: `BOT_USE_WEBHOOK=true`, `BOT_WEBHOOK_URL=https://your-domain.com/tg`, перед `gateway-tg` ставим reverse-proxy (caddy / nginx) с HTTPS.

---

## 16. SDD-workflow и шаблон спеки

### 16.1 Процесс
1. Идея → `specs/NNN-name.md` (черновик)
2. Ревью спеки
3. Тесты под `Acceptance criteria` (failing)
4. Имплементация до зелёных тестов
5. Если в процессе спека дрейфует — **сначала** правится спека, **потом** код

### 16.2 Шаблон

```markdown
# NNN. <Feature name>

**Status:** draft | in-review | implemented
**Author:** <name>
**Created:** YYYY-MM-DD

## Why
1-3 предложения: какую проблему решает.

## User flow
Шаги от лица пользователя.

## Technical flow
Шаги внутри системы: какие события публикуются, кем потребляются, что пишется в БД.

## Acceptance criteria
- [ ] критерий 1 (тестируется)
- [ ] критерий 2
- [ ] ...

## Data changes
Новые таблицы / колонки / события шины.

## Out of scope
Что в эту спеку НЕ входит (для будущих).

## Open questions
Что не решено и блокирует разработку.
```

---

## 17. Initial feature specs

Полные спеки первых четырёх фич — `specs/001` .. `specs/004`. Здесь даю их кратко, в репозитории они будут отдельными файлами.

### 17.1 `specs/001-customer-menu.md`

**Why.** Заказчику нужна точка входа во все взаимодействия — без перехода в ЛС, без отдельных команд.

**User flow.** В группе заказчика в `📋 Меню` (бывший General) висит запиненное сообщение бота. Заказчик жмёт кнопки, бот меняет текст и кнопки этого же сообщения, отображая нужный экран.

**Acceptance criteria:**
- [ ] При первом запуске бот создаёт меню-сообщение в General и пинит
- [ ] General закрыт для всех кроме админов и бота
- [ ] Экраны: `main`, `creating_prompt`, `my_tickets`, `closed_tickets`, `help`
- [ ] Переключение между экранами — `editMessageText`, не `sendMessage`
- [ ] FSM-состояние per (user_id, chat_id) хранится в Redis db=1 и БД (`fsm_state`)
- [ ] При нажатии callback'а бот вызывает `answerCallbackQuery` в течение 3 сек

**Data changes:** таблица `fsm_state`.

### 17.2 `specs/002-create-ticket.md`

**Why.** Главная операция, ради которой всё затевалось.

**User flow.** Кнопка «🆕 Новый тикет» → бот открывает General → просит описание ответом → создаёт топик → закрывает General → возвращает в `main`.

**Technical flow.** См. [§7.2](#72-флоу-создать-тикет) этого документа.

**Acceptance criteria:**
- [ ] Бот публикует `cmd.tg.reopen_general_forum_topic` после нажатия кнопки
- [ ] FSM-состояние пользователя становится `creating_prompt` с TTL=120 сек
- [ ] Если в 120 сек не пришёл ответ — General закрывается, состояние → `main`, тост «⏱ Время вышло»
- [ ] При получении ответа бот создаёт тикет, топик, шапку, чистит сообщение заказчика и системные сообщения о open/close General
- [ ] Шапка тикетного топика запинена
- [ ] Публикуется `events.ticket.created`
- [ ] В Sheets появляется новая строка (проверяется e2e-тестом против мок-Sheets)

**Data changes:** таблица `tickets`, событие `ticket.created`.

### 17.3 `specs/003-take-ticket.md`

**Why.** Без этого тикет не может перейти в работу.

**User flow.** В уведомлении в `🆕 Входящие` показаны кнопки с именами всех активных исполнителей. Любой из них (включая тимлида) жмёт на любое имя — назначает себя или коллегу. Тикет переходит в `in_progress`.

**Acceptance criteria:**
- [ ] В уведомлении показываются кнопки с именами всех активных исполнителей (`is_active=true` AND `telegram_user_id IS NOT NULL`), по 3 в ряд
- [ ] Исполнитель из YAML, у которого ещё не резолвлен `telegram_user_id`, кнопкой не показывается; в логи пишется WARNING
- [ ] Callback с кнопки имени обрабатывается только если `from_user.id` есть среди активных исполнителей; иначе toast «Вы не в списке исполнителей»
- [ ] Любой исполнитель может назначить как себя, так и другого — один интерфейс
- [ ] Если тикет уже назначен — toast «Уже взят: <имя>», без изменений
- [ ] При успехе: `tickets.assignee_id`, `tickets.status='in_progress'`, `tickets.in_progress_at`
- [ ] В `ticket_events` пишется запись с `actor_user_id` (кто нажал) и `assignee_id` в payload — для аудита
- [ ] Уведомление во `Входящих` редактируется: кнопки имён убираются, текст «✅ Взят: <имя>», остаётся `[🔗 Открыть тикет]`
- [ ] Иконка тикетного топика → `TOPIC_ICON_IN_PROGRESS`
- [ ] Шапка тикетного топика обновляется (статус «🟡 В работе»)
- [ ] Публикуется `events.ticket.assigned` с `assignee_user_id` и `assigned_by_user_id`
- [ ] Sheets обновляется (assignee, in_progress_at)

**Data changes:** колонки `tickets.assignee_id`, `tickets.status`, `tickets.in_progress_at`. Событие `ticket.assigned` (с новым полем `assigned_by_user_id`).

### 17.4 `specs/004-close-ticket.md`

**Why.** Завершение жизненного цикла.

**User flow.** Заказчик в тикетном топике жмёт «✅ Закрыть тикет» на шапке → подтверждение → закрытие.

**Acceptance criteria:**
- [ ] Кнопка «Закрыть» нажата кем-то, кроме `created_by_user_id` — toast «Закрыть может только заказчик», без изменений
- [ ] Заказчик жмёт «Закрыть» → шапка редактируется на «Уверены? [Да] [Отмена]»
- [ ] «Отмена» → шапка возвращается в нормальный вид
- [ ] «Да, закрыть» → `tickets.status=closed`, `closed_at`, `closed_by_user_id`
- [ ] Имя топика меняется на `[✅] #{id} {title}`
- [ ] Иконка топика → `TOPIC_ICON_CLOSED`
- [ ] Шапка обновляется в финальное состояние (без активных кнопок)
- [ ] В топик отправляется финальное сообщение «Тикет закрыт. Спасибо!»
- [ ] Топик закрывается (`closeForumTopic`)
- [ ] Публикуется `events.ticket.closed`
- [ ] Sheets обновляется

**Data changes:** колонки `tickets.status`, `tickets.closed_at`, `tickets.closed_by_user_id`. Событие `ticket.closed`.

### 17.5 `specs/005-onboard-customer.md`

**Why.** Должен быть простой способ подключить нового заказчика, не лазая в БД руками.

**User flow.** Админ создаёт форум-группу, добавляет бота. Бот сам предлагает завершить настройку или выдаёт чек-лист недостающих условий.

**Technical flow.** См. §3.5.

**Acceptance criteria:**
- [ ] При получении `events.tg.bot_membership_changed` с `new_status=administrator` core запускает онбординг
- [ ] Если `chat.is_forum=false` — бот пишет в General инструкцию включить форум-режим и выходит без записи в `customers`
- [ ] Если каких-то прав у бота нет — бот выводит чек-лист недостающих прав и кнопку «🔄 Проверить ещё раз»
- [ ] Если `chat_id` уже в `customers` — бот пишет «Уже подключено как '<title>'», ничего не пишет в БД
- [ ] При успехе: INSERT в `customers` (title=chat.title), General переименован в `📋 Меню`, главное меню запинено, General закрыт
- [ ] `customers.menu_message_id` и `customers.onboarded_at` заполнены
- [ ] Системные сообщения о close/edit General удалены
- [ ] Команда `/setup` от исполнителя приводит к тому же эффекту что автозапуск
- [ ] Команда `/setup` от не-исполнителя молча игнорируется
- [ ] Если бота кикнули из группы (`new_status=left|kicked`) — пишется WARNING в `🤖 Логи`, `is_active` не меняется

**Data changes:** колонки `customers.menu_message_id`, `customers.onboarded_at`. Новое событие `tg.bot_membership_changed`, команда `cmd.tg.edit_general_forum_topic`.

### 17.6 `specs/006-onboard-team-group.md`

**Why.** Одноразовая настройка командной группы без редактирования БД.

**User flow.** Админ создаёт форум-группу, добавляет бота, пишет `/setup_team_group` → бот создаёт топики и выдаёт готовый блок env-переменных для копирования в `.env`.

**Acceptance criteria:**
- [ ] Команда `/setup_team_group` доступна только исполнителям из `executors.yaml`
- [ ] Если `EXECUTOR_GROUP_CHAT_ID` уже задан и не совпадает с текущим — отказ с пояснением
- [ ] Бот создаёт топики `🆕 Входящие`, `🚨 Эскалации`, `📊 Сводка`, `🤖 Логи` (Общее — дефолтный General)
- [ ] В чат выводится готовый блок `EXECUTOR_GROUP_*` для копирования в `.env`
- [ ] Команда `/print_topic_id` работает в любом топике и печатает `message_thread_id`
- [ ] На старте `core` валидирует все `EXECUTOR_GROUP_*`: если что-то не сходится — ERROR в логи, сервис продолжает работать (клиентские группы обслуживает)

**Data changes:** нет в БД. Изменения в `.env`.

---

## 18. Открытые вопросы

Решения отложены сознательно — фиксируются здесь, чтобы вернуться при необходимости.

1. **Конкретные иконки топиков.** Нужно один раз посмотреть `getForumTopicIconStickers` и выбрать три подходящие — записать их `custom_emoji_id` в env.
2. **Контент `help`-экрана** для заказчика в главном меню группы.
3. **Содержимое `📊 Сводка`.** Что именно бот пишет ежедневно: счётчики, или ещё и список незакрытых > 7 дней, или статистика по исполнителям.
4. **Управление списком исполнителей в рантайме.** В v1 — через YAML + рестарт `core`. Если будет нужен интерактивный менеджмент без перезапуска — добавим топик `⚙️ Команда` в командной группе с пинными кнопками add/remove. Не сейчас.
5. **Переназначение/отмена назначения.** Сейчас никак (только руками в БД). Если кейс «исполнитель не сможет, надо передать» окажется частым — добавим кнопку `Передать` на assigned-уведомлении.
6. **Тимлид-функции.** В v1 тимлид = обычный исполнитель, флаг `is_lead` в YAML есть, но прав не даёт. Если понадобится принудительное переназначение или закрытие — отдельная спека.
7. **Эскалации / SLA.** Топик `🚨 Эскалации` зарезервирован, логики нет. Возможный триггер: тикет в `in_progress` > N дней без сообщений в топике. Не сейчас.
8. **«Свой» список тикетов для исполнителя.** В v1 нет — листают `Входящие`. Если станет неудобно — добавим топик `📌 В работе` с автообновляемым списком. Без ЛС.
9. **Хранение вложений.** Telegram сам хранит, в БД мы не кладём. Если потребуется поиск по вложениям — отдельная история.
10. **Имена тикетов > 128 символов** (лимит Telegram на имя топика). Сейчас: первая строка ответа — title, обрезаем до 128. Подумать: может, отдельно спрашивать title и description.
11. **Множественный заказчик в группе.** Спецификация предполагает «один заказчик на группу». Если в группе окажется два member'а — оба могут создавать тикеты, но закрыть тикет может только тот, кто его создал (`closed_by` сравнивается с `created_by_user_id`).

---

*Конец документа.*
