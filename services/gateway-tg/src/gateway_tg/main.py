"""Entrypoint gateway-tg.

В v0 — заглушка: загружает конфиг, инициализирует логирование и спит, чтобы
сервис был частью compose-стека до появления реальных обработчиков (handlers/,
executors/). Реализация — в рамках spec 001+.
"""

from __future__ import annotations

import asyncio
from typing import cast

from shared.logging import LogFormat, configure_logging

from gateway_tg.config import Settings


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="gateway-tg",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info(
        "gateway-tg starting (skeleton)",
        webhook=settings.bot_use_webhook,
    )
    # TODO(spec 001): инициализировать aiogram Bot/Dispatcher, подписаться на cmd.tg.*,
    # публиковать events.tg.*. Сейчас просто ждём, чтобы контейнер не падал.
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("gateway-tg stopping")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
