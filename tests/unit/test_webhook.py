"""Unit-тесты webhook prod-режима (gateway-tg, 8b).

Тестируем что aiohttp-приложение собирается и health-endpoint отвечает.
Полноценный e2e-тест webhook-пути требует подмены `bot.set_webhook`, его не
делаем здесь — он проверится при реальном деплое.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiohttp.test_utils import TestClient, TestServer
from gateway_tg.inbound.webhook import WEBHOOK_PATH, build_webhook_app


def _fake_bot() -> Bot:
    # AsyncMock без spec — у aiogram setup_application есть хук на bot.session.close()
    # при shutdown, который spec=Bot не покрывает.
    return AsyncMock()  # type: ignore[return-value]


def _fake_dp() -> Dispatcher:
    return Dispatcher()


async def test_health_endpoint() -> None:
    app = build_webhook_app(_fake_bot(), _fake_dp(), secret_token=None)
    async with TestServer(app) as server, TestClient(server) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        assert (await resp.text()) == "ok"


async def test_webhook_path_rejects_get() -> None:
    """``/tg`` — POST only, GET должен вернуть 405."""

    app = build_webhook_app(_fake_bot(), _fake_dp(), secret_token=None)
    async with TestServer(app) as server, TestClient(server) as client:
        resp = await client.get(WEBHOOK_PATH)
        assert resp.status == 405


async def test_webhook_path_rejects_wrong_secret() -> None:
    """Запрос без правильного X-Telegram-Bot-Api-Secret-Token не доходит до dispatcher."""

    app = build_webhook_app(_fake_bot(), _fake_dp(), secret_token="my-secret")
    async with TestServer(app) as server, TestClient(server) as client:
        # Без заголовка
        resp = await client.post(WEBHOOK_PATH, json={"update_id": 1})
        # aiogram возвращает 401, если secret_token не совпал.
        assert resp.status in (401, 403)
