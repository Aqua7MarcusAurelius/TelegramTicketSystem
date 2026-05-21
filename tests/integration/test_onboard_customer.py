"""Integration-тесты для spec 005 — onboard customer group."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from core.domain.onboarding import GENERAL_MENU_NAME, MissingRights
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
    async def test_happy_path(self, session: AsyncSession, use_case: OnboardCustomer) -> None:
        result = await use_case.from_membership_event(_membership_event())
        await session.commit()

        assert isinstance(result, OnboardResult)
        assert result.customer_created is True

        types = [type(c) for c in result.commands]
        assert types == [
            CmdEditGeneralForumTopic,
            CmdSendMessage,
            CmdCloseGeneralForumTopic,
        ]
        rename = result.commands[0]
        assert isinstance(rename, CmdEditGeneralForumTopic)
        assert rename.name == GENERAL_MENU_NAME

        send = result.commands[1]
        assert isinstance(send, CmdSendMessage)
        assert send.correlation_id is not None  # связь с phase 2

        # Customer создан с menu_correlation_id и без menu_message_id
        customers_repo = CustomersRepository(session)
        c = await customers_repo.get_by_chat(GROUP_CHAT_ID)
        assert c is not None
        assert c.title == GROUP_TITLE
        assert c.menu_correlation_id == send.correlation_id
        assert c.menu_message_id is None
        assert c.onboarded_at is not None

    async def test_not_administrator_skipped(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_membership_event(_membership_event(new_status="member"))
        assert isinstance(result, OnboardSkipped)
        assert result.reason == "not_administrator"

    async def test_not_forum_sends_instruction(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_membership_event(_membership_event(is_forum=False))
        await session.commit()
        assert isinstance(result, OnboardResult)
        assert result.customer_created is False
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "форума" in msg.text  # упомянут режим форума
        # Customer НЕ создан
        c = await CustomersRepository(session).get_by_chat(GROUP_CHAT_ID)
        assert c is None

    async def test_missing_rights_emits_checklist_with_button(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        result = await use_case.from_membership_event(
            _membership_event(can_pin_messages=False, can_delete_messages=False)
        )
        await session.commit()
        assert isinstance(result, OnboardResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "Pin Messages" in msg.text
        assert "Delete Messages" in msg.text
        kb = msg.reply_markup["inline_keyboard"]  # type: ignore[index]
        # одна кнопка с callback_data setup_recheck
        assert kb[0][0]["callback_data"] == "setup_recheck"
        # Customer НЕ создан
        assert await CustomersRepository(session).get_by_chat(GROUP_CHAT_ID) is None

    async def test_already_registered_says_already_connected(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        existing = Customer(
            telegram_chat_id=GROUP_CHAT_ID,
            title="Уже было",
            menu_message_id=99,  # уже завершённый onboarding
            onboarded_at=datetime.now(UTC),
        )
        session.add(existing)
        await session.commit()

        result = await use_case.from_membership_event(_membership_event())
        await session.commit()
        assert isinstance(result, OnboardResult)
        assert result.customer_created is False
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "Уже было" in msg.text

    async def test_existing_without_menu_message_id_re_onboards(
        self, session: AsyncSession, use_case: OnboardCustomer
    ) -> None:
        """Если customer был создан, но menu_message_id NULL (retry, рестарт) —
        повторный onboarding допустим, чтобы добить отправку меню."""

        existing = Customer(
            telegram_chat_id=GROUP_CHAT_ID,
            title=GROUP_TITLE,
            menu_message_id=None,
            onboarded_at=datetime.now(UTC),
        )
        session.add(existing)
        await session.commit()

        result = await use_case.from_membership_event(_membership_event())
        await session.commit()
        assert isinstance(result, OnboardResult)
        assert result.customer_created is False  # уже была запись
        # Но команды отправлены — повторно отправляем меню
        types = [type(c) for c in result.commands]
        assert CmdEditGeneralForumTopic in types
        assert CmdSendMessage in types
        assert CmdCloseGeneralForumTopic in types
        # menu_correlation_id обновлён
        c = await CustomersRepository(session).get_by_chat(GROUP_CHAT_ID)
        assert c is not None
        assert c.menu_correlation_id is not None

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
        # Сначала — onboarding до отправки
        use_case = OnboardCustomer(
            session=session,
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.from_membership_event(_membership_event())
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
