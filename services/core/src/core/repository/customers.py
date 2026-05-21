"""Repository для ``customers``. SPEC §10.2."""

from __future__ import annotations

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
