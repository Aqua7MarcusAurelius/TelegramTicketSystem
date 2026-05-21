"""Фикстуры интеграционных тестов.

Требуется поднятый Postgres из ``docker compose up -d postgres``. DSN можно
переопределить через ``TEST_POSTGRES_DSN``; по умолчанию — локальный compose.

Перед каждым тестом полностью чистим доменные таблицы (TRUNCATE).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from shared.db import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

DEFAULT_DSN = "postgresql+asyncpg://tickets:tickets@localhost:5432/tickets"

CORE_TABLES = (
    "ticket_events",
    "tickets",
    "fsm_state",
    "team_group_topic_setup",
    "core_processed_events",
    "executors",
    "customers",
)


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    dsn = os.environ.get("TEST_POSTGRES_DSN", DEFAULT_DSN)
    eng = build_engine(dsn)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return build_session_factory(engine)


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests(engine: AsyncEngine) -> AsyncIterator[None]:
    """Гарантирует чистую БД для каждого теста.

    Запуск ДО теста — на случай, если предыдущий тест упал и не убрался;
    если бы делали ПОСЛЕ, успех первого прогона зависел бы от состояния
    предыдущих сессий.
    """

    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(CORE_TABLES)} RESTART IDENTITY CASCADE")
        )
    yield


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Сессия для теста. Коммитит сама — тестируем production-flow ``await commit()``."""

    async with session_factory() as s:
        yield s
        await s.rollback()  # на случай, если тест что-то не докоммитил
