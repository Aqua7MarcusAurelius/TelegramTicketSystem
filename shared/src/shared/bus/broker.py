"""Конструктор FastStream-брокера на Redis Streams.

Соглашения (SPEC §4.2, §9):
- Redis ``db=0`` — стримы FastStream.
- Каждый сервис создаёт свой брокер через :func:`build_broker`, подписки и публикации
  оформляются в собственных модулях сервиса.
"""

from __future__ import annotations

from faststream.redis import RedisBroker


def redis_streams_url(redis_url: str) -> str:
    """Привести базовый ``REDIS_URL`` к URL стрим-инстанса (db=0).

    Поддерживаем оба варианта: с уже указанной db и без неё.
    """

    base = redis_url.rstrip("/")
    # Если уже есть номер базы — заменяем на 0.
    parts = base.rsplit("/", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return f"{parts[0]}/0"
    return f"{base}/0"


def build_broker(redis_url: str) -> RedisBroker:
    """Создать брокер для текущего сервиса.

    Логирование подключения остаётся на стороне сервиса (см. shared.logging).
    """

    return RedisBroker(redis_streams_url(redis_url))
