"""Repository для ``tickets`` — только READ-операции в рамках spec 001.

Логика создания/обновления тикетов — в spec 002+.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import Ticket, TicketStatus


class TicketsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_by_user(
        self,
        *,
        customer_id: int,
        created_by_user_id: int,
        offset: int = 0,
        limit: int = 10,
    ) -> Sequence[Ticket]:
        """Активные тикеты заказчика (status != closed), отсортированы по убыванию id."""

        stmt = (
            select(Ticket)
            .where(Ticket.customer_id == customer_id)
            .where(Ticket.created_by_user_id == created_by_user_id)
            .where(Ticket.status != TicketStatus.CLOSED)
            .order_by(Ticket.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def count_active_by_user(
        self,
        *,
        customer_id: int,
        created_by_user_id: int,
    ) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count(Ticket.id))
            .where(Ticket.customer_id == customer_id)
            .where(Ticket.created_by_user_id == created_by_user_id)
            .where(Ticket.status != TicketStatus.CLOSED)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_closed_by_user(
        self,
        *,
        customer_id: int,
        created_by_user_id: int,
        offset: int = 0,
        limit: int = 10,
    ) -> Sequence[Ticket]:
        """Закрытые тикеты заказчика за всё время.

        За «последние 30 дней» фильтрацию делаем в сервисе — repository не знает
        про текущее время, чтобы оставаться тестируемым без freezegun.
        """

        stmt = (
            select(Ticket)
            .where(Ticket.customer_id == customer_id)
            .where(Ticket.created_by_user_id == created_by_user_id)
            .where(Ticket.status == TicketStatus.CLOSED)
            .order_by(Ticket.closed_at.desc().nulls_last(), Ticket.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())
