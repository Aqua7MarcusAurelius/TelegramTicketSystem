"""FastStream-подписчик на ``events.tg.callback``.

Подписка регистрируется в :mod:`core.main` при старте.
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgCallback
from shared.events.streams import (
    CMD_TG_ANSWER_CALLBACK_QUERY,
    CMD_TG_EDIT_MESSAGE_TEXT,
    TG_CALLBACK,
)
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
    """Зарегистрировать подписчика на ``events.tg.callback``.

    Один обработчик на сервис, consumer-group по умолчанию (`core`).
    """

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
                if cmd.__class__.__name__ == "CmdEditMessageText":
                    await broker.publish(cmd, stream=CMD_TG_EDIT_MESSAGE_TEXT)
                elif cmd.__class__.__name__ == "CmdAnswerCallbackQuery":
                    await broker.publish(cmd, stream=CMD_TG_ANSWER_CALLBACK_QUERY)
            return

        # Skipped — может содержать toast (например, «уже закрыт»).
        if result.answer is not None:
            await broker.publish(result.answer, stream=CMD_TG_ANSWER_CALLBACK_QUERY)
        else:
            log.debug("menu_callback_skipped", reason=result.reason, event_id=event.event_id)
