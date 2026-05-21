"""Async SQLAlchemy engine / session factory.

Каждый сервис создаёт собственный engine с помощью :func:`build_engine` и
получает session-factory через :func:`build_session_factory`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Создать async-engine.

    ``dsn`` должен быть в формате ``postgresql+asyncpg://...``.
    """

    return create_async_engine(
        dsn,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
