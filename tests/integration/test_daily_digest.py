"""Integration-тесты use-case'а BuildDailyDigest (spec 9)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from core.repository.models import Customer, Ticket, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository
from core.services.daily_digest import BuildDailyDigest, DigestResult, DigestSkipped
from shared.events import CmdSendMessage, DailyDigestTick
from sqlalchemy.ext.asyncio import AsyncSession

TEAM_CHAT_ID = -1009999999999
TEAM_TOPIC_DIGEST = 3


async def _seed_customer(session: AsyncSession) -> Customer:
    c = Customer(
        telegram_chat_id=-1001234567890,
        title="X",
        menu_message_id=7,
        onboarded_at=datetime.now(UTC),
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


async def _seed_tickets(
    session: AsyncSession,
    customer_id: int,
    *,
    new: int,
    in_progress: int,
    closed: int,
) -> None:
    next_topic = 1000
    for _ in range(new):
        session.add(
            Ticket(
                customer_id=customer_id,
                topic_id=next_topic,
                title="t",
                description="d",
                status=TicketStatus.NEW,
                created_by_user_id=111,
            )
        )
        next_topic += 1
    for _ in range(in_progress):
        session.add(
            Ticket(
                customer_id=customer_id,
                topic_id=next_topic,
                title="t",
                description="d",
                status=TicketStatus.IN_PROGRESS,
                created_by_user_id=111,
                in_progress_at=datetime.now(UTC),
            )
        )
        next_topic += 1
    for _ in range(closed):
        session.add(
            Ticket(
                customer_id=customer_id,
                topic_id=next_topic,
                title="t",
                description="d",
                status=TicketStatus.CLOSED,
                created_by_user_id=111,
                closed_at=datetime.now(UTC),
            )
        )
        next_topic += 1
    await session.commit()


class TestBuildDailyDigest:
    async def test_counts_all_statuses(self, session: AsyncSession) -> None:
        c = await _seed_customer(session)
        await _seed_tickets(session, c.id, new=3, in_progress=2, closed=5)

        result = await BuildDailyDigest(
            session=session,
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=TEAM_CHAT_ID,
            executor_group_topic_digest=TEAM_TOPIC_DIGEST,
        ).execute(DailyDigestTick())
        await session.commit()

        assert isinstance(result, DigestResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert msg.chat_id == TEAM_CHAT_ID
        assert msg.topic_id == TEAM_TOPIC_DIGEST
        assert "Новых: <b>3</b>" in msg.text
        assert "В работе: <b>2</b>" in msg.text
        assert "Закрытых (всего): <b>5</b>" in msg.text

    async def test_skipped_if_team_group_not_configured(self, session: AsyncSession) -> None:
        result = await BuildDailyDigest(
            session=session,
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=None,
            executor_group_topic_digest=None,
        ).execute(DailyDigestTick())
        await session.commit()
        assert isinstance(result, DigestSkipped)
        assert result.reason == "team_group_not_configured"

    async def test_idempotency(self, session: AsyncSession) -> None:
        c = await _seed_customer(session)
        await _seed_tickets(session, c.id, new=1, in_progress=0, closed=0)

        event = DailyDigestTick(event_id=uuid4())
        use_case = BuildDailyDigest(
            session=session,
            processed=ProcessedEventsRepository(session),
            executor_group_chat_id=TEAM_CHAT_ID,
            executor_group_topic_digest=TEAM_TOPIC_DIGEST,
        )
        first = await use_case.execute(event)
        await session.commit()
        assert isinstance(first, DigestResult)

        second = await use_case.execute(event)
        await session.commit()
        assert isinstance(second, DigestSkipped)
        assert second.reason == "already_processed"
