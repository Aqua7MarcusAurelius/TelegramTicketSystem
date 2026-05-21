"""Entrypoint sheets-sync. v0 — заглушка."""

from __future__ import annotations

import asyncio
from typing import cast

from shared.logging import LogFormat, configure_logging

from sheets_sync.config import Settings


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="sheets-sync",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info(
        "sheets-sync starting (skeleton)",
        sheets_configured=bool(settings.google_sheets_id),
    )

    # TODO(spec 002+): подписаться на events.ticket.*, обернуть gspread в
    # asyncio.to_thread, поддерживать однопоточный writer.
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("sheets-sync stopping")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
