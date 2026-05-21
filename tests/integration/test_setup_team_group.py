"""Integration-тесты для spec 006 — onboard team group."""

from __future__ import annotations

from uuid import uuid4

import pytest
from core.domain.team_group import TOPIC_ORDER
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.team_group import TeamGroupSetupRepository
from core.services.setup_team_group import (
    AttachTeamTopic,
    PrintTopicId,
    SetupTeamGroup,
    TeamGroupResult,
    TeamGroupSkipped,
)
from shared.events import (
    CmdCreateForumTopic,
    CmdSendMessage,
    TgMessage,
    TgTopicCreated,
)
from sqlalchemy.ext.asyncio import AsyncSession

TEAM_CHAT_ID = -1009999999999
USER_IVAN = 5550001


def _setup_command_event(text: str = "/setup_team_group") -> TgMessage:
    return TgMessage(
        event_id=uuid4(),
        chat_id=TEAM_CHAT_ID,
        chat_type="supergroup",
        is_forum=True,
        topic_id=None,
        user_id=USER_IVAN,
        username="ivan",
        full_name="Иван",
        is_anonymous_admin=False,
        is_bot=False,
        is_service_message=False,
        service_message_type=None,
        text=text,
        message_id=1,
        reply_to_message_id=None,
    )


@pytest.fixture
def setup_use_case(session: AsyncSession) -> SetupTeamGroup:
    return SetupTeamGroup(
        repo=TeamGroupSetupRepository(session),
        processed=ProcessedEventsRepository(session),
        configured_chat_id=None,
    )


class TestSetupTeamGroup:
    async def test_happy_path_emits_4_creates(
        self, session: AsyncSession, setup_use_case: SetupTeamGroup
    ) -> None:
        result = await setup_use_case.execute(_setup_command_event())
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        assert len(result.commands) == 4
        for cmd in result.commands:
            assert isinstance(cmd, CmdCreateForumTopic)
            assert cmd.chat_id == TEAM_CHAT_ID
            assert cmd.correlation_id is not None
        # имена в порядке TOPIC_ORDER
        names = [c.name for c in result.commands]  # type: ignore[attr-defined]
        assert names[0].startswith("🆕")
        assert names[-1].startswith("🤖")

        rows = await TeamGroupSetupRepository(session).list_for_chat(TEAM_CHAT_ID)
        assert len(rows) == 4
        roles = {r.role for r in rows}
        assert roles == {r.value for r in TOPIC_ORDER}

    async def test_not_forum(self, session: AsyncSession) -> None:
        use_case = SetupTeamGroup(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
            configured_chat_id=None,
        )
        event = _setup_command_event()
        event = event.model_copy(update={"is_forum": False})
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "форума" in msg.text
        # БД не тронута
        rows = await TeamGroupSetupRepository(session).list_for_chat(TEAM_CHAT_ID)
        assert rows == []

    async def test_chat_id_mismatch(self, session: AsyncSession) -> None:
        use_case = SetupTeamGroup(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
            configured_chat_id=-1001234567890,  # другой
        )
        result = await use_case.execute(_setup_command_event())
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "EXECUTOR_GROUP_CHAT_ID" in msg.text
        rows = await TeamGroupSetupRepository(session).list_for_chat(TEAM_CHAT_ID)
        assert rows == []

    async def test_idempotency(self, session: AsyncSession, setup_use_case: SetupTeamGroup) -> None:
        event = _setup_command_event()
        first = await setup_use_case.execute(event)
        await session.commit()
        assert isinstance(first, TeamGroupResult)

        second = await setup_use_case.execute(event)
        await session.commit()
        assert isinstance(second, TeamGroupSkipped)
        assert second.reason == "already_processed"

    async def test_pending_run_blocks_second_attempt(
        self, session: AsyncSession, setup_use_case: SetupTeamGroup
    ) -> None:
        """Если уже была попытка с незакрытыми строками — повтор отказывает с месседжем."""

        await setup_use_case.execute(_setup_command_event())
        await session.commit()
        # Имитируем новый event_id (повторная команда после ошибки)
        result = await setup_use_case.execute(_setup_command_event())
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "Уже создаём топики" in msg.text


class TestAttachTeamTopic:
    async def test_partial_creates_no_env_block_yet(self, session: AsyncSession) -> None:
        # Сначала запустим setup
        setup = SetupTeamGroup(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
            configured_chat_id=None,
        )
        r = await setup.execute(_setup_command_event())
        await session.commit()
        assert isinstance(r, TeamGroupResult)
        create_cmds = [c for c in r.commands if isinstance(c, CmdCreateForumTopic)]

        # Симулируем только 2 ответа из 4
        attach = AttachTeamTopic(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        for i, c in enumerate(create_cmds[:2]):
            assert c.correlation_id is not None
            result = await attach.execute(
                TgTopicCreated(
                    correlation_id=c.correlation_id,
                    chat_id=TEAM_CHAT_ID,
                    topic_id=100 + i,
                    name=c.name,
                )
            )
            await session.commit()
            assert isinstance(result, TeamGroupResult)
            assert result.commands == ()  # env-блок ещё не публикуем

    async def test_all_creates_emit_env_block(self, session: AsyncSession) -> None:
        setup = SetupTeamGroup(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
            configured_chat_id=None,
        )
        r = await setup.execute(_setup_command_event())
        await session.commit()
        assert isinstance(r, TeamGroupResult)
        create_cmds = [c for c in r.commands if isinstance(c, CmdCreateForumTopic)]
        assert len(create_cmds) == 4

        attach = AttachTeamTopic(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        last_result: TeamGroupResult | TeamGroupSkipped | None = None
        for i, c in enumerate(create_cmds):
            assert c.correlation_id is not None
            last_result = await attach.execute(
                TgTopicCreated(
                    correlation_id=c.correlation_id,
                    chat_id=TEAM_CHAT_ID,
                    topic_id=1000 + i,
                    name=c.name,
                )
            )
            await session.commit()

        assert isinstance(last_result, TeamGroupResult)
        assert len(last_result.commands) == 1
        env = last_result.commands[0]
        assert isinstance(env, CmdSendMessage)
        # Все 3 env-ключа в блоке (Escalations пока не идёт в env)
        assert "EXECUTOR_GROUP_CHAT_ID" in env.text
        assert "EXECUTOR_GROUP_TOPIC_INCOMING" in env.text
        assert "EXECUTOR_GROUP_TOPIC_DIGEST" in env.text
        assert "EXECUTOR_GROUP_TOPIC_LOGS" in env.text

        # finished_at заполнен у всех строк
        rows = await TeamGroupSetupRepository(session).list_for_chat(TEAM_CHAT_ID)
        assert all(r.finished_at is not None for r in rows)

    async def test_unknown_correlation_skipped(self, session: AsyncSession) -> None:
        attach = AttachTeamTopic(
            repo=TeamGroupSetupRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await attach.execute(
            TgTopicCreated(
                correlation_id=uuid4(),
                chat_id=TEAM_CHAT_ID,
                topic_id=1,
                name="x",
            )
        )
        assert isinstance(result, TeamGroupSkipped)
        assert result.reason == "unknown_correlation"


class TestPrintTopicId:
    async def test_returns_topic_id(self, session: AsyncSession) -> None:
        use_case = PrintTopicId(processed=ProcessedEventsRepository(session))
        event = _setup_command_event(text="/print_topic_id")
        event = event.model_copy(update={"topic_id": 42})
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "42" in msg.text
        assert msg.topic_id == 42

    async def test_in_general_returns_no_thread_id(self, session: AsyncSession) -> None:
        use_case = PrintTopicId(processed=ProcessedEventsRepository(session))
        event = _setup_command_event(text="/print_topic_id")
        result = await use_case.execute(event)
        await session.commit()
        assert isinstance(result, TeamGroupResult)
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "General" in msg.text
