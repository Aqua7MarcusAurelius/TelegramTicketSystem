"""Хранение и чтение FSM single-message UI.

Ключ — пара ``(user_id, chat_id)``. См. SPEC §10.2, spec 001.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.menu import MenuState
from core.repository.models import FsmState


class FsmStateRepository:
    """Repository для таблицы ``fsm_state``.

    Не кэширует в Redis — это можно добавить позже, см. SPEC §4.2 (db=1).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int, chat_id: int) -> FsmState | None:
        stmt = select(FsmState).where(
            FsmState.user_id == user_id,
            FsmState.chat_id == chat_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_state(self, user_id: int, chat_id: int) -> MenuState:
        """Вернуть текущее состояние или ``MAIN`` по умолчанию.

        Если запись истекла (``expires_at < now()``) — состояние считается ``MAIN``
        (тайм-аут обработчик удалит запись отдельным вызовом ``expire``).
        """

        row = await self.get(user_id, chat_id)
        if row is None:
            return MenuState.MAIN
        if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
            return MenuState.MAIN
        try:
            return MenuState(row.state)
        except ValueError:
            # Unknown state в БД — лечим, считая MAIN.
            return MenuState.MAIN

    async def upsert(
        self,
        *,
        user_id: int,
        chat_id: int,
        state: MenuState,
        data: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Сохранить новое состояние пользователя.

        ``ttl_seconds`` нужно для состояний с тайм-аутом (например,
        ``creating_prompt`` — SPEC §7.2). Иначе истечение не выставляется.
        """

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        payload: dict[str, Any] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "state": state.value,
            "data": data or {},
            "updated_at": now,
            "expires_at": expires_at,
        }
        stmt = pg_insert(FsmState).values(**payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[FsmState.user_id, FsmState.chat_id],
            set_={
                "state": stmt.excluded.state,
                "data": stmt.excluded.data,
                "updated_at": stmt.excluded.updated_at,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        await self._session.execute(stmt)

    async def clear(self, user_id: int, chat_id: int) -> None:
        await self._session.execute(
            delete(FsmState).where(
                FsmState.user_id == user_id,
                FsmState.chat_id == chat_id,
            )
        )

    async def list_expired(self, *, limit: int = 100) -> list[FsmState]:
        """Список записей с истёкшим ``expires_at`` — для фоновой чистки."""

        stmt = (
            select(FsmState)
            .where(FsmState.expires_at.is_not(None))
            .where(FsmState.expires_at <= datetime.now(UTC))
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())
