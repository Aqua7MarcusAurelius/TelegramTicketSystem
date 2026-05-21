"""Entrypoint gateway-tg.

8a: long-poll, конвертация ``Update → events.tg.*``, выполнение 5 базовых
``cmd.tg.*``. Webhook-режим и форум-операции — в 8b.

Privacy mode у бота должен быть выключен через @BotFather (`/setprivacy →
Disable`), иначе bot не получает сообщения в группах. Проверить вручную.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from faststream import FastStream
from shared.bus import build_broker
from shared.logging import LogFormat, configure_logging

from gateway_tg.config import Settings
from gateway_tg.inbound.dispatcher import build_dispatcher
from gateway_tg.outbound import executor


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="gateway-tg",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("gateway-tg starting", webhook=settings.bot_use_webhook)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    broker = build_broker(settings.redis_url)
    executor.register(broker, bot)

    dp = build_dispatcher(broker)
    app = FastStream(broker)

    poll_task: asyncio.Task[None] | None = None
    if not settings.bot_use_webhook:
        # Long-poll (dev). В 8b добавим webhook-ветку.
        poll_task = asyncio.create_task(dp.start_polling(bot), name="aiogram-polling")

    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("gateway-tg stopping")
    finally:
        if poll_task is not None:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poll_task
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
