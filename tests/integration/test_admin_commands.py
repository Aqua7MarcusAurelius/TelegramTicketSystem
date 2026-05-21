"""Integration-тесты для spec 007 — admin-команды §3.7."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.domain.menu import MenuAction, MenuState, encode_callback
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.fsm import FsmStateRepository
from core.repository.models import Customer
from core.repository.processed_events import ProcessedEventsRepository
from core.repository.ticket_events import TicketEventsRepository
from core.repository.tickets import TicketsRepository
from core.services.admin_commands import (
    ActivateCustomer,
    AdminResult,
    DeactivateCustomer,
    ListCustomers,
    ReloadExecutors,
    RenameCustomer,
)
from core.services.create_ticket import CreateTicketPhase1, TicketSkipped
from core.services.handle_menu_callback import HandleMenuCallback, Skipped
from core.services.load_executors import ExecutorYaml, sync_executors
from shared.events import (
    CmdAnswerCallbackQuery,
    CmdSendMessage,
    TgCallback,
    TgMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_USER_ID = 5550001  # ivan, executor
NON_EXEC_USER_ID = 9999
CUSTOMER_CHAT_ID = -1001234567890
CUSTOMER_TITLE = "Marketing"


async def _seed_executor(session: AsyncSession) -> None:
    repo = ExecutorsRepository(session)
    await sync_executors(repo, [ExecutorYaml(username="ivan", full_name="Иван", is_lead=True)])
    await session.commit()
    await repo.resolve_user_id("ivan", ADMIN_USER_ID)
    await session.commit()


async def _seed_customer(
    session: AsyncSession, *, is_active: bool = True, chat_id: int = CUSTOMER_CHAT_ID
) -> Customer:
    c = Customer(
        telegram_chat_id=chat_id,
        title=CUSTOMER_TITLE,
        is_active=is_active,
        menu_message_id=7,
        onboarded_at=datetime.now(UTC),
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


def _admin_msg(text: str, *, chat_id: int = -1009999999999) -> TgMessage:
    return TgMessage(
        event_id=uuid4(),
        chat_id=chat_id,
        chat_type="supergroup",
        is_forum=True,
        topic_id=None,
        user_id=ADMIN_USER_ID,
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


class TestRenameCustomer:
    async def test_happy_path(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        use_case = RenameCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(
            _admin_msg(f'/rename_customer {CUSTOMER_CHAT_ID} "Новое имя"')
        )
        await session.commit()
        assert isinstance(result, AdminResult)
        assert len(result.commands) == 1
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "Новое имя" in msg.text
        # БД
        c = await CustomersRepository(session).get_by_chat(CUSTOMER_CHAT_ID)
        assert c is not None
        assert c.title == "Новое имя"

    async def test_unknown_customer(self, session: AsyncSession) -> None:
        use_case = RenameCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg('/rename_customer -1009999 "X"'))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "не зарегистрирован" in msg.text

    async def test_invalid_args_shows_usage(self, session: AsyncSession) -> None:
        use_case = RenameCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg("/rename_customer"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "Использование" in msg.text


class TestDeactivateActivate:
    async def test_deactivate(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        use_case = DeactivateCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg(f"/deactivate_customer {CUSTOMER_CHAT_ID}"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "неактивный" in msg.text
        c = await CustomersRepository(session).get_by_chat(CUSTOMER_CHAT_ID)
        assert c is not None
        assert c.is_active is False

    async def test_activate(self, session: AsyncSession) -> None:
        await _seed_customer(session, is_active=False)
        use_case = ActivateCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg(f"/activate_customer {CUSTOMER_CHAT_ID}"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "активирован" in msg.text
        c = await CustomersRepository(session).get_by_chat(CUSTOMER_CHAT_ID)
        assert c is not None
        assert c.is_active is True


class TestReloadExecutors:
    async def test_happy_path(self, session: AsyncSession) -> None:
        # Создадим временный YAML с 2 исполнителями
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                """\
executors:
  - username: ivan
    full_name: Иван
  - username: maria
    full_name: Мария
"""
            )
            path = f.name
        try:
            use_case = ReloadExecutors(
                executors=ExecutorsRepository(session),
                processed=ProcessedEventsRepository(session),
                config_path=path,
            )
            result = await use_case.execute(_admin_msg("/reload_executors"))
            await session.commit()
            msg = result.commands[0]
            assert isinstance(msg, CmdSendMessage)
            assert "2 записей" in msg.text
            # БД
            assert await ExecutorsRepository(session).get_by_username("ivan") is not None
            assert await ExecutorsRepository(session).get_by_username("maria") is not None
        finally:
            Path(path).unlink(missing_ok=True)  # noqa: ASYNC240

    async def test_missing_file(self, session: AsyncSession) -> None:
        use_case = ReloadExecutors(
            executors=ExecutorsRepository(session),
            processed=ProcessedEventsRepository(session),
            config_path="/nonexistent/path.yaml",
        )
        result = await use_case.execute(_admin_msg("/reload_executors"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "не найден" in msg.text


class TestListCustomers:
    async def test_empty(self, session: AsyncSession) -> None:
        use_case = ListCustomers(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg("/list_customers"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "нет" in msg.text

    async def test_lists_all(self, session: AsyncSession) -> None:
        await _seed_customer(session, chat_id=-1001)
        await _seed_customer(session, chat_id=-1002, is_active=False)
        use_case = ListCustomers(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        result = await use_case.execute(_admin_msg("/list_customers"))
        await session.commit()
        msg = result.commands[0]
        assert isinstance(msg, CmdSendMessage)
        assert "-1001" in msg.text
        assert "-1002" in msg.text
        # активный/неактивный отображаются разными иконками
        assert "✅" in msg.text
        assert "⛔" in msg.text


class TestDeactivatedCustomerGates:
    """Проверка что деактивированный заказчик не может пользоваться меню и создать тикет."""

    async def test_menu_callback_returns_inactive_toast(self, session: AsyncSession) -> None:
        await _seed_customer(session, is_active=False)
        use_case = HandleMenuCallback(
            customers=CustomersRepository(session),
            fsm=FsmStateRepository(session),
            tickets=TicketsRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        callback = TgCallback(
            event_id=uuid4(),
            chat_id=CUSTOMER_CHAT_ID,
            chat_type="supergroup",
            topic_id=None,
            user_id=11111,
            message_id=7,
            callback_data=encode_callback(MenuAction.HELP),
            callback_query_id="cb-1",
        )
        result = await use_case.execute(callback)
        await session.commit()
        assert isinstance(result, Skipped)
        assert result.reason == "customer_inactive"
        assert result.answer is not None
        assert isinstance(result.answer, CmdAnswerCallbackQuery)
        assert "отключена" in (result.answer.text or "")

    async def test_create_ticket_blocked_for_inactive(self, session: AsyncSession) -> None:
        await _seed_customer(session, is_active=False)
        # ставим FSM=creating_prompt, чтобы дошли до проверки is_active
        fsm = FsmStateRepository(session)
        await fsm.upsert(
            user_id=11111,
            chat_id=CUSTOMER_CHAT_ID,
            state=MenuState.CREATING_PROMPT,
            ttl_seconds=120,
        )
        await session.commit()
        use_case = CreateTicketPhase1(
            customers=CustomersRepository(session),
            fsm=fsm,
            tickets=TicketsRepository(session),
            ticket_events=TicketEventsRepository(session),
            processed=ProcessedEventsRepository(session),
            topic_icon_new=None,
        )
        msg = TgMessage(
            event_id=uuid4(),
            chat_id=CUSTOMER_CHAT_ID,
            chat_type="supergroup",
            is_forum=True,
            topic_id=None,
            user_id=11111,
            username="customer",
            full_name="C",
            is_anonymous_admin=False,
            is_bot=False,
            is_service_message=False,
            service_message_type=None,
            text="Заголовок",
            message_id=42,
            reply_to_message_id=None,
        )
        result = await use_case.execute(msg)
        await session.commit()
        assert isinstance(result, TicketSkipped)
        assert result.reason == "customer_inactive"


class TestIdempotency:
    async def test_rename_idempotent(self, session: AsyncSession) -> None:
        await _seed_customer(session)
        await _seed_executor(session)  # для общей сцены, хоть и неиспользован
        use_case = RenameCustomer(
            customers=CustomersRepository(session),
            processed=ProcessedEventsRepository(session),
        )
        evt = _admin_msg(f'/rename_customer {CUSTOMER_CHAT_ID} "Один"')
        first = await use_case.execute(evt)
        await session.commit()
        assert len(first.commands) == 1

        # Тот же event_id → второй вызов ничего не возвращает
        second = await use_case.execute(evt)
        await session.commit()
        assert second.commands == ()
