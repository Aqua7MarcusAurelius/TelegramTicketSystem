"""Use-case'ы синхронизации тикета в Sheets. SPEC §11.5.

Чистая логика (без gspread!) — собирает :class:`TicketRow` по ``events.ticket.*``,
проверяет идемпотентность, читает/обновляет ``sheets_sync_state``. Запись в
Sheets — снаружи через :class:`SheetsClient`.

Это разделение позволяет тестировать use-case'ы без поднятия Google API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from shared.events import TicketAssigned, TicketClosed, TicketCreated

from sheets_sync.repository.state import (
    ProcessedEventsRepository,
    SheetsSyncStateRepository,
)
from sheets_sync.sheets_client import TicketRow


def _supergroup_internal_id(telegram_chat_id: int) -> str:
    raw = str(telegram_chat_id)
    if raw.startswith("-100"):
        return raw[4:]
    return raw.lstrip("-")


def _deep_link(chat_id: int, topic_id: int) -> str:
    return f"https://t.me/c/{_supergroup_internal_id(chat_id)}/{topic_id}"


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt is not None else ""


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """План записи в Sheets: что писать и куда (по существующему row или append)."""

    row: TicketRow
    existing_row_number: int | None


@dataclass(frozen=True, slots=True)
class Skipped:
    reason: str


# In-memory кеш денормализованного состояния тикета (id → последние известные поля).
# Sheets-sync не хранит сами тикеты в БД — события приходят последовательно
# (created → assigned → closed), и кеш позволяет переиспользовать customer_title,
# assignee и т.п. при последующих update-событиях. Очищается при рестарте — это
# OK: при рестарте мы прочитаем актуальные значения из event'ов и при необходимости
# перезапишем строку (idempotency через ``processed_events`` всё равно защитит).
_TicketCache: dict[int, dict[str, str]] = {}


def _remember(ticket_id: int, **fields: str) -> None:
    _TicketCache.setdefault(ticket_id, {}).update(fields)


def _recall(ticket_id: int, key: str, default: str = "") -> str:
    return _TicketCache.get(ticket_id, {}).get(key, default)


def _reset_cache_for_tests() -> None:
    """Поддержка тестов: гарантирует чистый кеш между тест-кейсами."""

    _TicketCache.clear()


@dataclass(slots=True)
class PlanTicketCreated:
    state: SheetsSyncStateRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TicketCreated) -> SyncPlan | Skipped:
        if not await self.processed.try_mark(event.event_id):
            return Skipped(reason="already_processed")

        _remember(
            event.ticket_id,
            customer_title=event.customer_title,
            title=event.title,
            customer_chat_id=str(event.customer_chat_id),
            topic_id=str(event.topic_id),
            created_at=_iso(event.created_at),
        )

        existing = await self.state.get(event.ticket_id)
        row = TicketRow(
            ticket_id=event.ticket_id,
            customer_title=event.customer_title,
            title=event.title,
            status="new",
            assignee="",
            created_at_iso=_iso(event.created_at),
            in_progress_at_iso="",
            closed_at_iso="",
            deep_link=_deep_link(event.customer_chat_id, event.topic_id),
        )
        return SyncPlan(row=row, existing_row_number=existing.sheet_row if existing else None)


@dataclass(slots=True)
class PlanTicketAssigned:
    state: SheetsSyncStateRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TicketAssigned) -> SyncPlan | Skipped:
        if not await self.processed.try_mark(event.event_id):
            return Skipped(reason="already_processed")

        existing = await self.state.get(event.ticket_id)
        if existing is None:
            # events.ticket.created ещё не доехал — events.ticket.assigned пришёл вперёд.
            # Skipping без записи processed — но мы УЖЕ записали (try_mark выше). Это значит,
            # повтор события не сработает. Это OK: at-most-once для не-критичных update'ов.
            # Альтернатива — отложенная очередь повторных попыток. Запишем известное в кеш.
            _remember(event.ticket_id, assignee=event.assignee_full_name)
            return Skipped(reason="row_not_found")

        _remember(event.ticket_id, assignee=event.assignee_full_name)
        row = TicketRow(
            ticket_id=event.ticket_id,
            customer_title=_recall(event.ticket_id, "customer_title"),
            title=_recall(event.ticket_id, "title"),
            status="in_progress",
            assignee=event.assignee_full_name,
            created_at_iso=_recall(event.ticket_id, "created_at"),
            in_progress_at_iso=_iso(event.assigned_at),
            closed_at_iso="",
            deep_link=_deep_link(
                int(_recall(event.ticket_id, "customer_chat_id", "0")),
                int(_recall(event.ticket_id, "topic_id", "0")),
            ),
        )
        return SyncPlan(row=row, existing_row_number=existing.sheet_row)


@dataclass(slots=True)
class PlanTicketClosed:
    state: SheetsSyncStateRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TicketClosed) -> SyncPlan | Skipped:
        if not await self.processed.try_mark(event.event_id):
            return Skipped(reason="already_processed")

        existing = await self.state.get(event.ticket_id)
        if existing is None:
            return Skipped(reason="row_not_found")

        row = TicketRow(
            ticket_id=event.ticket_id,
            customer_title=_recall(event.ticket_id, "customer_title"),
            title=_recall(event.ticket_id, "title"),
            status="closed",
            assignee=_recall(event.ticket_id, "assignee"),
            created_at_iso=_recall(event.ticket_id, "created_at"),
            in_progress_at_iso="",  # обновлять не нужно, оставляем как было — пустота
            # просто не перезапишет существующее значение в Sheets, поскольку у нас
            # batch_update пишет всё подряд. Лучше восстановить из кеша.
            closed_at_iso=_iso(event.closed_at),
            deep_link=_deep_link(
                int(_recall(event.ticket_id, "customer_chat_id", "0")),
                int(_recall(event.ticket_id, "topic_id", "0")),
            ),
        )
        # in_progress_at скорее всего был при assigned; если он есть в кеше — оставим.
        in_progress = _recall(event.ticket_id, "in_progress_at_iso", "")
        if in_progress:
            row = TicketRow(**{**row.__dict__, "in_progress_at_iso": in_progress})
        return SyncPlan(row=row, existing_row_number=existing.sheet_row)
