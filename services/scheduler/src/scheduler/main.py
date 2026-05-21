"""Entrypoint scheduler. SPEC §11.4.

APScheduler с Postgres job store — переживает рестарт. Один job:
``daily_digest`` — по cron из env ``DIGEST_CRON`` (по умолчанию ``0 9 * * *``)
публикует :class:`DailyDigestTick` в шину. Подписан core — он соберёт сводку
и отправит в командную группу.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import cast
from zoneinfo import ZoneInfo

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # type: ignore[import-untyped]
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from faststream import FastStream
from shared.bus import build_broker
from shared.events import DailyDigestTick
from shared.events.dispatch import stream_for
from shared.logging import LogFormat, configure_logging

from scheduler.config import Settings

log = structlog.get_logger(__name__)


def _sync_dsn(async_dsn: str) -> str:
    """APScheduler ждёт sync-DSN. Меняем драйвер ``+asyncpg`` → ``+psycopg2``."""

    return re.sub(r"\+asyncpg(?=:|$)", "+psycopg2", async_dsn)


async def _publish_daily_digest_tick(broker) -> None:
    """Job-функция: публикует event в шину."""

    event = DailyDigestTick()
    await broker.publish(event, stream=stream_for(event))
    log.info("daily_digest_tick_published", event_id=str(event.event_id))


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log_root = configure_logging(
        service="scheduler",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log_root.info(
        "scheduler starting",
        cron=settings.digest_cron,
        tz=settings.tz,
    )

    broker = build_broker(settings.redis_url)
    app = FastStream(broker)

    timezone = ZoneInfo(settings.tz)
    aps = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=_sync_dsn(settings.postgres_dsn))},
        timezone=timezone,
    )

    # Job регистрируется идемпотентно — APScheduler сам обновит trigger,
    # если cron изменился.
    aps.add_job(
        _publish_daily_digest_tick,
        trigger=CronTrigger.from_crontab(settings.digest_cron, timezone=timezone),
        id="daily_digest",
        replace_existing=True,
        kwargs={"broker": broker},
    )
    aps.start()
    log_root.info("daily_digest_scheduled")

    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log_root.info("scheduler stopping")
    finally:
        aps.shutdown(wait=False)
        with contextlib.suppress(Exception):
            await broker.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
