"""Repository для ``tickets``.

READ-операции добавлены в spec 001, write-операции — в spec 002 (создание).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import Ticket, TicketStatus


class TicketsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        customer_id: int,
        title: str,
        description: str,
        created_by_user_id: int,
        create_correlation_id: UUID,
    ) -> Ticket:
        """Создать тикет в статусе ``new`` без ``topic_id`` (spec 002, фаза 1).

        ``topic_id`` заполнится фазой 2 на ``events.tg.topic_created`` по
        ``create_correlation_id``.
        """

        ticket = Ticket(
            customer_id=customer_id,
            topic_id=None,
            title=title,
            description=description,
            status=TicketStatus.NEW,
            created_by_user_id=created_by_user_id,
            create_correlation_id=create_correlation_id,
        )
        self._session.add(ticket)
        await self._session.flush()  # нужен ticket.id для дальнейших действий
        return ticket

    async def get(self, ticket_id: int) -> Ticket | None:
        return await self._session.get(Ticket, ticket_id)

    async def get_by_correlation(self, correlation_id: UUID) -> Ticket | None:
        stmt = select(Ticket).where(Ticket.create_correlation_id == correlation_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def set_topic(self, ticket_id: int, topic_id: int) -> None:
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is not None:
            ticket.topic_id = topic_id

    async def set_header(self, ticket_id: int, header_message_id: int) -> None:
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is not None:
            ticket.header_message_id = header_message_id

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
