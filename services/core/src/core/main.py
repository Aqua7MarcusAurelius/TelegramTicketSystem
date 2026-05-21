"""Entrypoint core.

v0 — заглушка: загружает конфиг, поднимает логи, валидирует наличие командной
группы (см. SPEC §3.6: при отсутствии env — ERROR, но НЕ падаем). Реальные
подписки появятся в рамках spec 001+.
"""

from __future__ import annotations

import asyncio
from typing import cast

from shared.logging import LogFormat, configure_logging

from core.config import Settings


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="core",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("core starting (skeleton)")

    if settings.executor_group_chat_id is None:
        # Не падаем — позволяем сервис-стеку подняться и обслуживать клиентские группы
        # после исполнения /setup_team_group и заполнения .env. См. SPEC §3.6.
        log.error(
            "EXECUTOR_GROUP_CHAT_ID is not set — notifications to team group will be skipped"
        )

    # TODO(spec 001): подписки на events.tg.*, events.tg.bot_membership_changed,
    # запуск FSM-движка single-message UI.
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("core stopping")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
