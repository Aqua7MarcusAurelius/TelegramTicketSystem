"""Unit-тесты scheduler (spec 9). Тестируем:
- конвертацию asyncpg-DSN в sync-DSN для APScheduler
- публикацию ``DailyDigestTick`` через мок-broker
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from scheduler.main import _publish_daily_digest_tick, _sync_dsn
from shared.events import DailyDigestTick


@pytest.mark.parametrize(
    ("asyncpg_dsn", "expected"),
    [
        (
            "postgresql+asyncpg://u:p@host:5432/db",
            "postgresql+psycopg2://u:p@host:5432/db",
        ),
        ("postgresql+asyncpg://", "postgresql+psycopg2://"),
        # Уже sync — не трогаем
        ("postgresql://u@h/db", "postgresql://u@h/db"),
    ],
)
def test_sync_dsn(asyncpg_dsn: str, expected: str) -> None:
    assert _sync_dsn(asyncpg_dsn) == expected


async def test_publish_daily_digest_tick_emits_event() -> None:
    broker = AsyncMock()
    await _publish_daily_digest_tick(broker)

    broker.publish.assert_awaited_once()
    event = broker.publish.await_args.args[0]
    assert isinstance(event, DailyDigestTick)
    assert broker.publish.await_args.kwargs["stream"] == "events.schedule.daily_digest"
