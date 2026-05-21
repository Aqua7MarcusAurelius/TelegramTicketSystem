"""FastStream-подписчик на ``events.tg.callback``.

Подписка регистрируется в :mod:`core.main` при старте.
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgCallback
from shared.events.dispatch import stream_for
from shared.events.streams import TG_CALLBACK
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.handle_menu_callback import (
    HandleMenuCallback,
    MenuCallbackResult,
)

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    """Зарегистрировать подписчика на ``events.tg.callback``."""

    @broker.subscriber(stream=TG_CALLBACK, group="core")
    async def on_tg_callback(event: TgCallback) -> None:
        async with session_factory() as session:
            use_case = HandleMenuCallback(
                customers=CustomersRepository(session),
                fsm=FsmStateRepository(session),
                tickets=TicketsRepository(session),
                processed=ProcessedEventsRepository(session),
            )
            result = await use_case.execute(event)
            await session.commit()

        if isinstance(result, MenuCallbackResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            return

        # Skipped — может содержать toast.
        if result.answer is not None:
            await broker.publish(result.answer, stream=stream_for(result.answer))
        else:
            log.debug("menu_callback_skipped", reason=result.reason, event_id=event.event_id)
