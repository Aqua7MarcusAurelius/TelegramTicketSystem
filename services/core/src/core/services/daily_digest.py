"""Use-case: ежедневная сводка в `📊 Сводка`. SPEC §11.4.

В v1 — простой формат: три счётчика (новых тикетов / в работе / закрытых).
Открытый вопрос §18.3 даёт право выбора более детального содержимого позже —
пока минимум, который не зависит от внешних сервисов.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.events import CmdSendMessage, DailyDigestTick, Event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.repository.models import Ticket, TicketStatus
from core.repository.processed_events import ProcessedEventsRepository


@dataclass(frozen=True, slots=True)
class DigestResult:
    commands: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class DigestSkipped:
    reason: str


@dataclass(slots=True)
class BuildDailyDigest:
    session: AsyncSession
    processed: ProcessedEventsRepository
    executor_group_chat_id: int | None
    executor_group_topic_digest: int | None

    async def execute(self, event: DailyDigestTick) -> DigestResult | DigestSkipped:
        if not await self.processed.try_mark(event.event_id):
            return DigestSkipped(reason="already_processed")
        if self.executor_group_chat_id is None or self.executor_group_topic_digest is None:
            return DigestSkipped(reason="team_group_not_configured")

        counts = dict(
            (
                await self.session.execute(
                    select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
                )
            ).all()
        )
        new_count = counts.get(TicketStatus.NEW, 0)
        in_progress_count = counts.get(TicketStatus.IN_PROGRESS, 0)
        closed_count = counts.get(TicketStatus.CLOSED, 0)

        text = (
            "📊 <b>Ежедневная сводка</b>\n"
            "\n"
            f"⚪ Новых: <b>{new_count}</b>\n"
            f"🟡 В работе: <b>{in_progress_count}</b>\n"
            f"✅ Закрытых (всего): <b>{closed_count}</b>"
        )

        return DigestResult(
            commands=(
                CmdSendMessage(
                    chat_id=self.executor_group_chat_id,
                    topic_id=self.executor_group_topic_digest,
                    text=text,
                    parse_mode="HTML",
                ),
            )
        )
