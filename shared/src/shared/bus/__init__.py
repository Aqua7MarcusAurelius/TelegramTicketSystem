"""FastStream / Redis Streams — обёртка над общими настройками шины.

См. docs/SPEC.md §9. Стримы Redis живут в ``db=0``; для них используется отдельный URL.
"""

from shared.bus.broker import build_broker, redis_streams_url

__all__ = ["build_broker", "redis_streams_url"]
