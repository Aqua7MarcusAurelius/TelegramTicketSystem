"""Entrypoint gateway-tg.

Два режима, выбор через ``BOT_USE_WEBHOOK``:
- ``false`` (dev) — long-poll через ``dp.start_polling``.
- ``true``  (prod) — aiohttp-app на ``:8080``, регистрируем webhook у Telegram.

В обоих режимах параллельно живёт FastStream broker, слушающий ``cmd.tg.*``
команды и публикующий ``events.tg.*``.

Privacy mode у бота должен быть выключен через @BotFather, иначе он не получает
сообщения в группах.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from faststream import FastStream
from shared.bus import build_broker
from shared.logging import LogFormat, configure_logging

from gateway_tg.config import Settings
from gateway_tg.inbound.dispatcher import build_dispatcher
from gateway_tg.inbound.webhook import (
    build_webhook_app,
    deregister_webhook,
    register_webhook,
)
from gateway_tg.outbound import executor

WEBHOOK_BIND_HOST = "0.0.0.0"
WEBHOOK_BIND_PORT = 8080


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
    web_runner: web.AppRunner | None = None

    if settings.bot_use_webhook:
        if not settings.bot_webhook_url:
            log.error("bot_webhook_url_missing")
            raise SystemExit(1)
        await register_webhook(
            bot,
            url=settings.bot_webhook_url,
            secret_token=settings.bot_webhook_secret,
        )
        web_app = build_webhook_app(bot, dp, secret_token=settings.bot_webhook_secret)
        web_runner = web.AppRunner(web_app)
        await web_runner.setup()
        site = web.TCPSite(web_runner, WEBHOOK_BIND_HOST, WEBHOOK_BIND_PORT)
        await site.start()
        log.info("webhook_server_listening", host=WEBHOOK_BIND_HOST, port=WEBHOOK_BIND_PORT)
    else:
        # Long-poll (dev).
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
        if web_runner is not None:
            await web_runner.cleanup()
        if settings.bot_use_webhook:
            await deregister_webhook(bot)
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
