"""Webhook prod-режим. Spec 008b.

aiogram предоставляет ``SimpleRequestHandler``, который сам разбирает входящие
JSON-апдейты и валидирует secret token через заголовок
``X-Telegram-Bot-Api-Secret-Token``. Мы поднимаем aiohttp-app, регистрируем
этот handler на одном endpoint'е, и на старте регистрируем webhook у Telegram.

При остановке корректно сносим webhook через ``deleteWebhook`` — иначе Telegram
будет ломиться на (возможно) уже мёртвый URL.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

WEBHOOK_PATH = "/tg"
"""Путь, на который Telegram шлёт POST. Должен совпадать с публичной частью URL."""

log = structlog.get_logger(__name__)


def build_webhook_app(
    bot: Bot,
    dp: Dispatcher,
    secret_token: str | None,
) -> web.Application:
    """Собрать aiohttp.Application с зарегистрированным webhook-handler'ом.

    Если ``secret_token`` задан, aiogram сверяет его с заголовком
    ``X-Telegram-Bot-Api-Secret-Token`` и отбрасывает чужие запросы.
    """

    app = web.Application()
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret_token)
    handler.register(app, path=WEBHOOK_PATH)

    # aiogram пристёгивает lifecycle-хуки к aiohttp.Application'у.
    setup_application(app, dp, bot=bot)

    # Простой healthcheck — пригодится reverse-proxy / k8s probes.
    async def _health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/health", _health)
    return app


async def register_webhook(bot: Bot, *, url: str, secret_token: str | None) -> None:
    """Зарегистрировать webhook у Telegram.

    ``url`` — публичный HTTPS-адрес сервиса (без пути; путь подставляется
    автоматически как :data:`WEBHOOK_PATH`).
    """

    target = url.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(
        url=target,
        secret_token=secret_token,
        drop_pending_updates=False,
        allowed_updates=["message", "edited_message", "callback_query", "my_chat_member"],
    )
    log.info("webhook_registered", url=target)


async def deregister_webhook(bot: Bot) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("webhook_deleted")
    except Exception:
        log.exception("webhook_delete_failed")
