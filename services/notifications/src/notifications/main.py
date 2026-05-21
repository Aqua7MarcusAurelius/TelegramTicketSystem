"""Entrypoint notifications. v0 — заглушка (см. main.py паттерн в gateway-tg/core)."""

from __future__ import annotations

import asyncio
from typing import cast

from shared.logging import LogFormat, configure_logging

from notifications.config import Settings


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="notifications",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("notifications starting (skeleton)")

    # TODO(spec 003): подписаться на events.ticket.* и публиковать cmd.tg.send_message
    # / cmd.tg.edit_message_text по правилам routes.py.
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("notifications stopping")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
