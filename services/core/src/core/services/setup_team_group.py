"""Use-case'ы онбординга командной группы. SPEC §3.6, spec 006.

- :class:`SetupTeamGroup` — обрабатывает команду ``/setup_team_group``.
- :class:`AttachTeamTopic` — обрабатывает ``events.tg.topic_created`` для
  топиков командной группы; когда все 4 топика созданы, публикует env-блок.
- :class:`PrintTopicId` — обработка ``/print_topic_id``.

Идемпотентность ``/setup_team_group`` обеспечивается ``processed_events`` —
повторная отправка той же команды на тот же chat не создаст 8 топиков
(вторая `add` упадёт на UNIQUE(chat_id, role)). Защитная проверка
«уже есть незавершённая попытка для этого чата» — тоже здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from shared.events import (
    CmdCreateForumTopic,
    CmdSendMessage,
    Event,
    TgMessage,
    TgTopicCreated,
)

from core.domain.team_group import (
    TOPIC_NAMES,
    TOPIC_ORDER,
    TeamGroupEnvBlock,
    TeamTopicRole,
    already_configured_text,
    not_forum_text,
    print_topic_id_text,
)
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.team_group import TeamGroupSetupRepository


@dataclass(frozen=True, slots=True)
class TeamGroupResult:
    commands: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class TeamGroupSkipped:
    reason: str
    commands: tuple[Event, ...] = ()


@dataclass(slots=True)
class SetupTeamGroup:
    repo: TeamGroupSetupRepository
    processed: ProcessedEventsRepository
    configured_chat_id: int | None
    """Текущее значение ``EXECUTOR_GROUP_CHAT_ID`` из env."""

    async def execute(self, event: TgMessage) -> TeamGroupResult | TeamGroupSkipped:
        if not await self.processed.try_mark(event.event_id):
            return TeamGroupSkipped(reason="already_processed")

        # 1) Конфликт с уже выставленным env
        if self.configured_chat_id is not None and self.configured_chat_id != event.chat_id:
            return TeamGroupResult(
                commands=(
                    CmdSendMessage(
                        chat_id=event.chat_id,
                        text=already_configured_text(self.configured_chat_id),
                        parse_mode="HTML",
                    ),
                )
            )

        # 2) Не форум
        if not event.is_forum:
            return TeamGroupResult(
                commands=(
                    CmdSendMessage(
                        chat_id=event.chat_id,
                        text=not_forum_text(),
                        parse_mode="HTML",
                    ),
                )
            )

        # 3) Уже была попытка для этого чата с незавершёнными строками?
        existing = await self.repo.list_for_chat(event.chat_id)
        if existing and any(r.finished_at is None for r in existing):
            return TeamGroupResult(
                commands=(
                    CmdSendMessage(
                        chat_id=event.chat_id,
                        text=(
                            "ℹ️ Уже создаём топики — ждите автоответ с env-блоком. "
                            "Если что-то пошло не так, удалите запись из БД и повторите."
                        ),
                        parse_mode="HTML",
                    ),
                )
            )

        # 4) Создание 4 топиков
        commands: list[Event] = []
        for role in TOPIC_ORDER:
            correlation = uuid4()
            await self.repo.add(
                chat_id=event.chat_id,
                correlation_id=correlation,
                role=role.value,
            )
            commands.append(
                CmdCreateForumTopic(
                    correlation_id=correlation,
                    chat_id=event.chat_id,
                    name=TOPIC_NAMES[role],
                )
            )
        return TeamGroupResult(commands=tuple(commands))


# ---------------------------------------------------------------------
# Phase 2: events.tg.topic_created
# ---------------------------------------------------------------------


@dataclass(slots=True)
class AttachTeamTopic:
    repo: TeamGroupSetupRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgTopicCreated) -> TeamGroupResult | TeamGroupSkipped:
        if event.correlation_id is None:
            return TeamGroupSkipped(reason="no_correlation")
        if not await self.processed.try_mark(event.event_id):
            return TeamGroupSkipped(reason="already_processed")

        row = await self.repo.get_by_correlation(event.correlation_id)
        if row is None:
            # Не наш correlation — обработает другой хендлер (tickets).
            return TeamGroupSkipped(reason="unknown_correlation")

        await self.repo.set_topic(event.correlation_id, event.topic_id)
        rows = await self.repo.list_for_chat(row.chat_id)

        # Все 4 заполнены?
        by_role: dict[str, int] = {r.role: r.topic_id for r in rows if r.topic_id is not None}
        required = {r.value for r in TOPIC_ORDER}
        if not required.issubset(by_role.keys()):
            return TeamGroupResult(commands=())

        await self.repo.mark_finished(row.chat_id)
        env = TeamGroupEnvBlock(
            chat_id=row.chat_id,
            incoming=by_role[TeamTopicRole.INCOMING.value],
            digest=by_role[TeamTopicRole.DIGEST.value],
            logs=by_role[TeamTopicRole.LOGS.value],
        )
        return TeamGroupResult(
            commands=(
                CmdSendMessage(
                    chat_id=row.chat_id,
                    text=env.render(),
                    parse_mode="HTML",
                ),
            )
        )


# ---------------------------------------------------------------------
# /print_topic_id — debug-утилита
# ---------------------------------------------------------------------


@dataclass(slots=True)
class PrintTopicId:
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessage) -> TeamGroupResult | TeamGroupSkipped:
        if not await self.processed.try_mark(event.event_id):
            return TeamGroupSkipped(reason="already_processed")
        return TeamGroupResult(
            commands=(
                CmdSendMessage(
                    chat_id=event.chat_id,
                    topic_id=event.topic_id,
                    text=print_topic_id_text(event.topic_id),
                    parse_mode="HTML",
                ),
            )
        )
