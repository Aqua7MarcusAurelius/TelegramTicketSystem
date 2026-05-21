"""Entrypoint sheets-sync. SPEC §11.5."""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

import structlog
from faststream import FastStream
from shared.bus import build_broker
from shared.db import build_engine, build_session_factory
from shared.logging import LogFormat, configure_logging

from sheets_sync.config import Settings
from sheets_sync.handlers import ticket_events
from sheets_sync.sheets_client import SheetsClient


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="sheets-sync",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info(
        "sheets-sync starting",
        sheets_configured=bool(
            settings.google_sheets_id and settings.google_sheets_credentials_json
        ),
    )

    engine = build_engine(settings.postgres_dsn)
    session_factory = build_session_factory(engine)

    client: SheetsClient | None = None
    if settings.google_sheets_id and settings.google_sheets_credentials_json:
        client = SheetsClient(
            credentials_json=settings.google_sheets_credentials_json,
            spreadsheet_id=settings.google_sheets_id,
        )
        try:
            await client.ensure_ready()
        except Exception:
            structlog.get_logger(__name__).exception("sheets_initial_open_failed")
            client = None

    broker = build_broker(settings.redis_url)
    worker_task = ticket_events.register(broker, session_factory, client)

    app = FastStream(broker)
    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("sheets-sync stopping")
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker_task
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
