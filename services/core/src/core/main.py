"""Entrypoint core.

Запуск:
- читает конфиг,
- настраивает логирование,
- поднимает SQLAlchemy engine + session factory,
- синхронизирует ``config/executors.yaml`` в БД (SPEC §3.4),
- поднимает FastStream broker и регистрирует handler'ы,
- запускает фоновую корутину для FSM expire (SPEC §7.2),
- логирует ERROR (но не падает) если ``EXECUTOR_GROUP_CHAT_ID`` не задан
  (SPEC §3.6).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import cast

from faststream import FastStream
from faststream.redis import RedisBroker
from shared.bus import build_broker
from shared.db import build_engine, build_session_factory
from shared.events.dispatch import stream_for
from shared.logging import LogFormat, configure_logging
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.handlers import (
    tg_callback,
    tg_message,
    tg_message_sent,
    tg_topic_created,
    ticket_assigned,
    ticket_created,
)
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.fsm import FsmStateRepository
from core.services.expire_creating_prompt import ExpireCreatingPrompt
from core.services.load_executors import parse_executors_yaml, sync_executors

EXPIRE_LOOP_INTERVAL_SECONDS = 15


async def _expire_loop(
    session_factory: async_sessionmaker,
    broker: RedisBroker,
    *,
    interval: int = EXPIRE_LOOP_INTERVAL_SECONDS,
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:
                use_case = ExpireCreatingPrompt(
                    fsm=FsmStateRepository(session),
                    customers=CustomersRepository(session),
                )
                commands = await use_case.run_once()
                await session.commit()
            for cmd in commands:
                await broker.publish(cmd, stream=stream_for(cmd))
        except asyncio.CancelledError:
            raise
        except Exception:
            import structlog

            structlog.get_logger(__name__).exception("expire_loop_iteration_failed")


async def _sync_executors_yaml(
    session_factory: async_sessionmaker,
    path: str,
) -> None:
    # Чтение конфига — разовая операция на старте, синхронный путь оправдан.
    p = Path(path)
    if not p.exists():  # noqa: ASYNC240
        import structlog

        structlog.get_logger(__name__).warning("executors_yaml_missing", path=path)
        return

    content = p.read_text(encoding="utf-8")  # noqa: ASYNC240
    items = parse_executors_yaml(content)
    async with session_factory() as session:
        await sync_executors(ExecutorsRepository(session), items)
        await session.commit()


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    log = configure_logging(
        service="core",
        level=settings.log_level,
        fmt=cast(LogFormat, settings.log_format),
    )
    log.info("core starting")

    if settings.executor_group_chat_id is None:
        log.error("EXECUTOR_GROUP_CHAT_ID is not set — team-group notifications will be skipped")

    engine = build_engine(settings.postgres_dsn)
    session_factory = build_session_factory(engine)

    await _sync_executors_yaml(session_factory, settings.executors_config_path)
    log.info("executors_sync_done", path=settings.executors_config_path)

    broker = build_broker(settings.redis_url)
    tg_callback.register(broker, session_factory, settings)
    tg_message.register(broker, session_factory, settings)
    tg_topic_created.register(broker, session_factory)
    tg_message_sent.register(broker, session_factory)
    ticket_created.register(broker, session_factory, settings)
    ticket_assigned.register(broker, session_factory, settings)

    app = FastStream(broker)
    expire_task = asyncio.create_task(_expire_loop(session_factory, broker), name="expire-loop")
    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("core stopping")
    finally:
        expire_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await expire_task
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
