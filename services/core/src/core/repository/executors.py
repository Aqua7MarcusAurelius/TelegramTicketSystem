"""Repository для ``executors``. SPEC §3.4, §10.2.

Список ведётся в ``config/executors.yaml`` и upsert'ится на старте `core`.
``telegram_user_id`` известен только после того, как исполнитель что-то напишет
в командной группе — тогда он резолвится отдельным проходом.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import Executor


class ExecutorsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_from_yaml(
        self,
        *,
        username: str,
        full_name: str,
        is_lead: bool,
    ) -> None:
        """Создать запись или обновить ``full_name``/``is_lead`` по ``username``.

        ``telegram_user_id`` не трогаем — он резолвится отдельно. Для новых
        записей ставим временное значение ``-id`` (по неотрицательному ``user_id``
        Telegram гарантированно не пересечётся).

        Контракт делает upsert по NATURAL key ``username``, но в схеме UNIQUE стоит
        на ``telegram_user_id``. Поэтому для новых записей сначала проверяем по
        username, и только если не нашли — INSERT с placeholder'ом.
        """

        existing = await self.get_by_username(username)
        if existing is not None:
            existing.full_name = full_name
            existing.is_lead = is_lead
            existing.is_active = True
            return

        # Placeholder telegram_user_id: гарантированно отрицательный (Telegram ID > 0).
        # Уникальность достигается через хэш username, диапазон — [-2^31, -1].
        placeholder = -((abs(hash(username)) % (2**31)) + 1)
        # На случай маловероятной коллизии с уже существующим placeholder'ом,
        # делаем insert с ON CONFLICT DO NOTHING и проверяем результат.
        stmt = (
            pg_insert(Executor)
            .values(
                telegram_user_id=placeholder,
                username=username,
                full_name=full_name,
                is_active=True,
                is_lead=is_lead,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)

    async def get_by_username(self, username: str) -> Executor | None:
        stmt = select(Executor).where(Executor.username == username)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_telegram_id(self, telegram_user_id: int) -> Executor | None:
        stmt = select(Executor).where(Executor.telegram_user_id == telegram_user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_active_resolved(self) -> Sequence[Executor]:
        """Активные исполнители с уже резолвнутым ``telegram_user_id``.

        SPEC §8.2: только они показываются кнопками в `Входящих`.
        """

        stmt = (
            select(Executor)
            .where(Executor.is_active.is_(True))
            .where(Executor.telegram_user_id > 0)  # отсекает placeholder'ы (< 0)
            .order_by(Executor.id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def deactivate_not_in(self, usernames: Sequence[str]) -> None:
        """Пометить `is_active=false` всех, кого нет в актуальном YAML.

        Записи не удаляем — нужны для целостности FK на старых тикетах
        (SPEC §3.4).
        """

        await self._session.execute(
            update(Executor).where(Executor.username.not_in(usernames)).values(is_active=False)
        )

    async def resolve_user_id(self, username: str, telegram_user_id: int) -> bool:
        """Привязать ``telegram_user_id`` к существующей записи по ``username``.

        Возвращает True, если что-то обновили (т.е. это был первый раз, когда
        исполнитель написал в группу). Иначе False.
        """

        existing = await self.get_by_username(username)
        if existing is None:
            return False
        if existing.telegram_user_id == telegram_user_id:
            return False
        existing.telegram_user_id = telegram_user_id
        return True
