"""Entrypoint core.

Запуск:
- читает конфиг,
- настраивает логирование,
- поднимает SQLAlchemy engine + session factory,
- поднимает FastStream broker и регистрирует handler'ы,
- логирует ERROR (но не падает) если ``EXECUTOR_GROUP_CHAT_ID`` не задан
  (SPEC §3.6: позволяем клиентским группам работать без командной).
"""

from __future__ import annotations

import asyncio
from typing import cast

from faststream import FastStream
from shared.bus import build_broker
from shared.db import build_engine, build_session_factory
from shared.logging import LogFormat, configure_logging

from core.config import Settings
from core.handlers import tg_callback


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="core",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("core starting")

    if settings.executor_group_chat_id is None:
        log.error("EXECUTOR_GROUP_CHAT_ID is not set — team-group notifications will be skipped")

    engine = build_engine(settings.postgres_dsn)
    session_factory = build_session_factory(engine)

    broker = build_broker(settings.redis_url)
    tg_callback.register(broker, session_factory)

    app = FastStream(broker)
    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("core stopping")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
