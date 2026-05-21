"""Подписчик на ``events.tg.bot_membership_changed``. Spec 005."""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.bus import stream_sub
from shared.events import TgBotMembershipChanged
from shared.events.dispatch import stream_for
from shared.events.streams import TG_BOT_MEMBERSHIP_CHANGED
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.services.onboard_customer import (
    OnboardCustomer,
    OnboardResult,
    log_bot_kicked,
)

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=stream_sub(TG_BOT_MEMBERSHIP_CHANGED, group="core"))
    async def on_membership_changed(event: TgBotMembershipChanged) -> None:
        # kick/left — отдельная короткая ветка
        if event.new_status in {"left", "kicked"}:
            log_bot_kicked(event)
            return

        async with session_factory() as session:
            use_case = OnboardCustomer(
                session=session,
                customers=CustomersRepository(session),
                processed=ProcessedEventsRepository(session),
            )
            result = await use_case.from_membership_event(event)
            await session.commit()

        if isinstance(result, OnboardResult):
            for cmd in result.commands:
                await broker.publish(cmd, stream=stream_for(cmd))
            if result.customer_created:
                log.info("customer_onboarded", chat_id=event.chat_id, title=event.chat_title)
            return
        log.debug("onboard_skipped", reason=result.reason, event_id=event.event_id)
