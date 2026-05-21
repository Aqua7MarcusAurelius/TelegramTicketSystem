"""Integration-тесты sheets-sync use-case'ов (spec 9, шаг 9).

Тестируем SyncPlan-логику против реальной БД без gspread — запись в Sheets
происходит снаружи use-case'а. Кеш `_TicketCache` чистим между тестами.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from shared.events import TicketAssigned, TicketClosed, TicketCreated
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
    _reset_cache_for_tests,
)
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Marketing"
TICKET_ID = 42


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _reset_cache_for_tests()


def _created_event(*, customer_title: str = CUSTOMER_TITLE) -> TicketCreated:
    return TicketCreated(
        ticket_id=TICKET_ID,
        customer_id=1,
        customer_chat_id=CUSTOMER_CHAT_ID,
        customer_title=customer_title,
        topic_id=200,
        title="Поправить шапку",
        description="детали",
        created_by_user_id=111,
        created_at=datetime.now(UTC),
    )


class TestPlanTicketCreated:
    async def test_first_seen_creates_plan_with_append(self, session: AsyncSession) -> None:
        plan = await PlanTicketCreated(
            state=SheetsSyncStateRepository(session),
            processed=ProcessedEventsRepository(session),
        ).execute(_created_event())
        await session.commit()

        assert isinstance(plan, SyncPlan)
        assert plan.existing_row_number is None  # append, не update
        assert plan.row.ticket_id == TICKET_ID
        assert plan.row.customer_title == CUSTOMER_TITLE
        assert plan.row.status == "new"
        assert plan.row.assignee == ""
        assert plan.row.deep_link.startswith("https://t.me/c/1234567890/")

    async def test_idempotency_on_repeat(self, session: AsyncSession) -> None:
        event = _created_event()
        first = await PlanTicketCreated(
            state=SheetsSyncStateRepository(session),
            processed=ProcessedEventsRepository(session),
        ).execute(event)
        await session.commit()
        assert isinstance(first, SyncPlan)

        second = await PlanTicketCreated(
            state=SheetsSyncStateRepository(session),
            processed=ProcessedEventsRepository(session),
        ).execute(event)
        await session.commit()
        assert isinstance(second, Skipped)
        assert second.reason == "already_processed"


class TestPlanTicketAssigned:
    async def test_updates_existing_row(self, session: AsyncSession) -> None:
        # Подсадим created → state и кеш
        state = SheetsSyncStateRepository(session)
        await PlanTicketCreated(state=state, processed=ProcessedEventsRepository(session)).execute(
            _created_event()
        )
        # Эмулируем, что worker записал row_number 5
        await state.upsert(ticket_id=TICKET_ID, sheet_row=5, last_event_id=uuid4())
        await session.commit()

        event = TicketAssigned(
            ticket_id=TICKET_ID,
            assignee_user_id=222,
            assignee_full_name="Мария Сидорова",
            assigned_by_user_id=222,
            assigned_at=datetime.now(UTC),
        )
        plan = await PlanTicketAssigned(
            state=state, processed=ProcessedEventsRepository(session)
        ).execute(event)
        await session.commit()
        assert isinstance(plan, SyncPlan)
        assert plan.existing_row_number == 5
        assert plan.row.status == "in_progress"
        assert plan.row.assignee == "Мария Сидорова"

    async def test_assigned_without_state_returns_row_not_found(
        self, session: AsyncSession
    ) -> None:
        event = TicketAssigned(
            ticket_id=TICKET_ID,
            assignee_user_id=222,
            assignee_full_name="X",
            assigned_by_user_id=222,
            assigned_at=datetime.now(UTC),
        )
        plan = await PlanTicketAssigned(
            state=SheetsSyncStateRepository(session),
            processed=ProcessedEventsRepository(session),
        ).execute(event)
        await session.commit()
        assert isinstance(plan, Skipped)
        assert plan.reason == "row_not_found"


class TestPlanTicketClosed:
    async def test_updates_to_closed(self, session: AsyncSession) -> None:
        state = SheetsSyncStateRepository(session)
        await PlanTicketCreated(state=state, processed=ProcessedEventsRepository(session)).execute(
            _created_event()
        )
        await state.upsert(ticket_id=TICKET_ID, sheet_row=7, last_event_id=uuid4())
        await session.commit()

        plan = await PlanTicketClosed(
            state=state, processed=ProcessedEventsRepository(session)
        ).execute(
            TicketClosed(
                ticket_id=TICKET_ID,
                closed_by_user_id=111,
                closed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        assert isinstance(plan, SyncPlan)
        assert plan.row.status == "closed"
        assert plan.row.closed_at_iso != ""
        assert plan.existing_row_number == 7

    async def test_closed_without_state_returns_row_not_found(self, session: AsyncSession) -> None:
        plan = await PlanTicketClosed(
            state=SheetsSyncStateRepository(session),
            processed=ProcessedEventsRepository(session),
        ).execute(
            TicketClosed(
                ticket_id=TICKET_ID,
                closed_by_user_id=111,
                closed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        assert isinstance(plan, Skipped)
        assert plan.reason == "row_not_found"
