"""Repository для ``customers``. SPEC §10.2."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import Customer


class CustomersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_chat(self, telegram_chat_id: int) -> Customer | None:
        stmt = select(Customer).where(Customer.telegram_chat_id == telegram_chat_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, customer_id: int) -> Customer | None:
        return await self._session.get(Customer, customer_id)

    async def get_by_menu_correlation(self, correlation_id: UUID) -> Customer | None:
        stmt = select(Customer).where(Customer.menu_correlation_id == correlation_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        telegram_chat_id: int,
        title: str,
        menu_correlation_id: UUID,
    ) -> Customer:
        """Создать запись заказчика, ставя ``menu_correlation_id`` для последующего pin."""

        customer = Customer(
            telegram_chat_id=telegram_chat_id,
            title=title,
            is_active=True,
            menu_message_id=None,
            menu_correlation_id=menu_correlation_id,
            onboarded_at=datetime.now(UTC),
        )
        self._session.add(customer)
        await self._session.flush()
        return customer

    async def set_menu_message_id(self, customer_id: int, message_id: int) -> None:
        customer = await self._session.get(Customer, customer_id)
        if customer is not None:
            customer.menu_message_id = message_id
