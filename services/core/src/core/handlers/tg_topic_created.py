"""Подписчик на ``events.tg.topic_created``.

Multiplexer по correlation_id:
- тикет (spec 002 phase 2) → :class:`HandleTopicCreated`
- topic командной группы (spec 006) → :class:`AttachTeamTopic`
"""

from __future__ import annotations

import structlog
from faststream.redis import RedisBroker
from shared.events import TgTopicCreated
from shared.events.dispatch import stream_for
from shared.events.streams import TG_TOPIC_CREATED
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.team_group import TeamGroupSetupRepository
from core.repository.tickets import TicketsRepository
from core.services.create_ticket import HandleTopicCreated, TicketResult
from core.services.setup_team_group import AttachTeamTopic, TeamGroupResult

log = structlog.get_logger(__name__)


def register(
    broker: RedisBroker,
    session_factory: async_sessionmaker,
) -> None:
    @broker.subscriber(stream=TG_TOPIC_CREATED, group="core")
    async def on_topic_created(event: TgTopicCreated) -> None:
        if event.correlation_id is None:
            return

        async with session_factory() as session:
            tickets = TicketsRepository(session)
            team_group = TeamGroupSetupRepository(session)
            processed = ProcessedEventsRepository(session)

            # 1) Тикет (spec 002)
            if (await tickets.get_by_correlation(event.correlation_id)) is not None:
                result_t = await HandleTopicCreated(
                    tickets=tickets,
                    customers=CustomersRepository(session),
                    processed=processed,
                ).execute(event)
                await session.commit()
                if isinstance(result_t, TicketResult):
                    for cmd in result_t.commands:
                        await broker.publish(cmd, stream=stream_for(cmd))
                else:
                    log.debug(
                        "topic_created_ticket_skipped",
                        reason=result_t.reason,
                        event_id=event.event_id,
                    )
                return

            # 2) Команда /setup_team_group (spec 006)
            if (await team_group.get_by_correlation(event.correlation_id)) is not None:
                result_g = await AttachTeamTopic(repo=team_group, processed=processed).execute(
                    event
                )
                await session.commit()
                if isinstance(result_g, TeamGroupResult):
                    for cmd in result_g.commands:
                        await broker.publish(cmd, stream=stream_for(cmd))
                else:
                    log.debug(
                        "topic_created_team_group_skipped",
                        reason=result_g.reason,
                        event_id=event.event_id,
                    )
                return

            log.debug("topic_created_unknown_correlation", event_id=event.event_id)
