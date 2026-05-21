"""Entrypoint scheduler. v0 — заглушка."""

from __future__ import annotations

import asyncio
from typing import cast

from shared.logging import LogFormat, configure_logging

from scheduler.config import Settings


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="scheduler",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("scheduler starting (skeleton)", cron=settings.digest_cron, tz=settings.tz)

    # TODO: настроить APScheduler с SQLAlchemyJobStore поверх Postgres, добавить
    # job daily_digest по cron из settings.digest_cron, публиковать
    # events.schedule.daily_digest.
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("scheduler stopping")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
