"""Integration-тесты для spec 005 — onboard customer group."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from core.domain.onboarding import MissingRights
from core.repository.customers import CustomersRepository
from core.repository.models import Customer
from core.repository.processed_events import ProcessedEventsRepository
from core.services.onboard_customer import (
    HandleMenuMessageSent,
    OnboardCustomer,
    OnboardResult,
    OnboardSkipped,
)
from shared.events import (
    CmdCloseGeneralForumTopic,
    CmdEditGeneralForumTopic,
    CmdPinMessage,
    CmdSendMessage,
    TgBotMembershipChanged,
    TgMessageSent,
)
from sqlalchemy.ext.asyncio import AsyncSession

GROUP_CHAT_ID = -1001234567890
GROUP_TITLE = "Marketing — Тикеты"
ACTOR_USER_ID = 12345


def _membership_event(
    *,
    new_status: str = "administrator",
    is_forum: bool = True,
    can_manage_topics: bool = True,
    can_delete_messages: bool = True,
    can_pin_messages: bool = True,
    title: str | None = GROUP_TITLE,
) -> TgBotMembershipChanged:
    return TgBotMembershipChanged(
        event_id=uuid4(),
        chat_id=GROUP_CHAT_ID,
        chat_type="supergroup",
        chat_title=title,
        is_forum=is_forum,
        old_status="left",
        new_status=new_status,  # type: ignore[arg-type]
        can_manage_topics=can_manage_topics,
        can_delete_messages=can_delete_messages,
        can_pin_messages=can_pin_messages,
        actor_user_id=ACTOR_USER_ID,
    )


@pytest.fixture
def use_case(session: AsyncSession) -> OnboardCustomer:
    return OnboardCustomer(
        session=session,
        customers=CustomersRepository(session),
        processed=ProcessedEventsRepository(session),
    )


class TestFromMembershipEvent:
    """Авто-onboarding убран (см. update spec 005). При новом администраторском
    statuse'е бот шлёт только подсказку с инструкцией — реальный onboarding
    запускает явная команда (`/setup` или `/setup_team_group`)."""

    async def test_admin_sends_hint(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_membership_event(_membership_event())
        await session.commit()

        assert isinstance(result, OnboardResult)
        assert result.customer_created is False
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "/setup" in msg.text
        assert "/setup_team_group" in msg.text
        # Customer НЕ зарегистрирован — бот не знает, какая это группа
        assert await CustomersRepository(session).get_by_chat(GROUP_CHAT_ID) is None

    async def test_not_administrator_skipped(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_membership_event(_membership_event(new_status="member"))
        assert isinstance(result, OnboardSkipped)
        assert result.reason == "not_administrator"

    async def test_already_onboarded_no_hint(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        """Группа уже зарегистрирована и menu_message_id заполнен —
        повторное событие (например, обновили права бота) не должно
        слать подсказку, иначе пользователь будет получать спам."""

        session.add(
            Customer(
                telegram_chat_id=GROUP_CHAT_ID,
                title="Already",
                menu_message_id=99,
                onboarded_at=datetime.now(UTC),
            )
        )
        await session.commit()

        result = await use_case.from_membership_event(_membership_event())
        await session.commit()
        assert isinstance(result, OnboardSkipped)
        assert result.reason == "already_onboarded"

    async def test_idempotency(self, session: AsyncSession, use_case: OnboardCustomer) -> None:
        event = _membership_event()
        first = await use_case.from_membership_event(event)
        await session.commit()
        assert isinstance(first, OnboardResult)

        second = await use_case.from_membership_event(event)
        await session.commit()
        assert isinstance(second, OnboardSkipped)
        assert second.reason == "already_processed"


class TestFromSetupCommand:
    async def test_emits_same_commands_as_membership_event(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_setup_command(
            chat_id=GROUP_CHAT_ID,
            chat_title=GROUP_TITLE,
            is_forum=True,
            rights=MissingRights(
                can_manage_topics=True,
                can_delete_messages=True,
                can_pin_messages=True,
            ),
        )
        await session.commit()
        assert isinstance(result, OnboardResult)
        assert result.customer_created is True
        types = [type(c) for c in result.commands]
        assert types == [
            CmdEditGeneralForumTopic,
            CmdSendMessage,
            CmdCloseGeneralForumTopic,
        ]


class TestHandleMenuMessageSent:
    async def test_pins_menu_after_send(self, session: AsyncSession) -> None:
        # Сначала — onboarding до отправки (через явный /setup)
        use_case = OnboardCustomer(
            session=session,
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.from_setup_command(
            chat_id=GROUP_CHAT_ID,
            chat_title=GROUP_TITLE,
            is_forum=True,
            rights=MissingRights(
                can_manage_topics=True,
                can_delete_messages=True,
                can_pin_messages=True,
            ),
        )
        await session.commit()
        assert isinstance(result, OnboardResult)
        send = next(c for c in result.commands if isinstance(c, CmdSendMessage))

        # Эмулируем events.tg.message_sent от gateway-tg
        msg_sent = TgMessageSent(
            correlation_id=send.correlation_id,
            chat_id=GROUP_CHAT_ID,
            topic_id=None,
            message_id=55555,
        )
        complete = HandleMenuMessageSent(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result2 = await complete.execute(msg_sent)
        await session.commit()
        assert isinstance(result2, OnboardResult)
        assert len(result2.commands) == 1
        pin = result2.commands[0]
        assert isinstance(pin, CmdPinMessage)
        assert pin.message_id == 55555

        # menu_message_id заполнен
        c = await CustomersRepository(session).get_by_chat(GROUP_CHAT_ID)
        assert c is not None
        assert c.menu_message_id == 55555

    async def test_unknown_correlation_skipped(self, session: AsyncSession) -> None:
        complete = HandleMenuMessageSent(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        msg = TgMessageSent(
            correlation_id=uuid4(),
            chat_id=GROUP_CHAT_ID,
            topic_id=None,
            message_id=1,
        )
        result = await complete.execute(msg)
        assert isinstance(result, OnboardSkipped)
        assert result.reason == "unknown_menu_correlation"
