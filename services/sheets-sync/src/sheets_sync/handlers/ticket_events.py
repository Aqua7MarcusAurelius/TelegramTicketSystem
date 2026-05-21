"""Подписчики на ``events.ticket.*``. SPEC §11.5.

Все три события (created/assigned/closed) кладутся в одну очередь и
обрабатываются последовательным воркером. Это нужно, потому что gspread
не атомарен на уровне «find row by ticket_id + update» — параллельная
обработка приводит к race условиям.
"""

from __future__ import annotations

import asyncio

import structlog
from faststream.redis import RedisBroker
from shared.events import TicketAssigned, TicketClosed, TicketCreated
from shared.events.streams import (
    TICKET_ASSIGNED,
    TICKET_CLOSED,
    TICKET_CREATED,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

from sheets_sync.repository.state import (
    ProcessedEventsRepository,
    SheetsSyncStateRepository,
)
from sheets_sync.services.sync_ticket import (
    PlanTicketAssigned,
    PlanTicketClosed,
    PlanTicketCreated,
    Skipped,
    SyncPlan,
)
from sheets_sync.sheets_client import SheetsClient

log = structlog.get_logger(__name__)

# Тип события очереди — мы не знаем, какой из трёх классов; faststream даст любой из них.
_QueueItem = TicketCreated | TicketAssigned | TicketClosed


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
    client: SheetsClient | None,
) -> asyncio.Task[None]:
    """Зарегистрировать подписчиков и запустить queue-worker.

    Возвращает worker-task — чтобы main мог его cancel'нуть на shutdown.

    Если ``client is None`` — Google Sheets не сконфигурирован, worker делает
    only-DB операции и не пишет в Sheets (чтобы можно было обкатать сервис
    без credentials).
    """

    queue: asyncio.Queue[_QueueItem] = asyncio.Queue()

    @broker.subscriber(stream=TICKET_CREATED, group="sheets-sync")
    async def _on_created(event: TicketCreated) -> None:
        await queue.put(event)

    @broker.subscriber(stream=TICKET_ASSIGNED, group="sheets-sync")
    async def _on_assigned(event: TicketAssigned) -> None:
        await queue.put(event)

    @broker.subscriber(stream=TICKET_CLOSED, group="sheets-sync")
    async def _on_closed(event: TicketClosed) -> None:
        await queue.put(event)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                await _process(item, session_factory, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sheets_sync_worker_failure", ticket_id=item.ticket_id)
            finally:
                queue.task_done()

    return asyncio.create_task(worker(), name="sheets-sync-worker")


async def _process(
    item: _QueueItem,
    session_factory: async_sessionmaker,
    client: SheetsClient | None,
) -> None:
    async with session_factory() as session:
        state = SheetsSyncStateRepository(session)
        processed = ProcessedEventsRepository(session)

        if isinstance(item, TicketCreated):
            plan = await PlanTicketCreated(state=state, processed=processed).execute(item)
        elif isinstance(item, TicketAssigned):
            plan = await PlanTicketAssigned(state=state, processed=processed).execute(item)
        elif isinstance(item, TicketClosed):
            plan = await PlanTicketClosed(state=state, processed=processed).execute(item)
        else:
            log.warning("sheets_sync_unknown_event", event=type(item).__name__)
            await session.commit()
            return

        if isinstance(plan, Skipped):
            log.debug("sheets_sync_skipped", reason=plan.reason, ticket_id=item.ticket_id)
            await session.commit()
            return

        # Записываем в Sheets вне сессии — внешний IO.
        new_row_number = await _write_to_sheets(plan, client)
        if new_row_number is not None:
            await state.upsert(
                ticket_id=plan.row.ticket_id,
                sheet_row=new_row_number,
                last_event_id=item.event_id,
            )
        await session.commit()


async def _write_to_sheets(plan: SyncPlan, client: SheetsClient | None) -> int | None:
    if client is None:
        # Без credentials просто симулируем: append → виртуальная row, update → та же.
        if plan.existing_row_number is None:
            return 2  # 1 строка заголовков + 1
        return plan.existing_row_number
    return await client.append_or_update(plan.row, existing_row_number=plan.existing_row_number)
