"""Use-case'ы admin-команд. SPEC §3.7, spec 007.

Все команды принимают :class:`TgMessage` и возвращают :class:`AdminResult` со
списком команд для публикации (обычно ровно один ``cmd.tg.send_message`` —
ответ в чат, откуда пришла команда).

Идемпотентность — через ``processed_events.try_mark`` на event_id. Проверка
«вызывающий — исполнитель» делает handler, потому что зависит от ExecutorsRepository
и общая для всех admin-команд (так не дублируем код).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.events import CmdSendMessage, Event, TgMessage

from core.domain.admin import (
    AdminCommandParseError,
    activated_text,
    customer_not_found_text,
    deactivated_text,
    list_customers_empty_text,
    list_customers_text,
    parse_chat_id_only,
    parse_rename_customer,
    reload_executors_missing_text,
    reload_executors_text,
    rename_success_text,
    usage_text,
)
from core.repository.customers import CustomersRepository
from core.repository.executors import ExecutorsRepository
from core.repository.processed_events import ProcessedEventsRepository
from core.services.load_executors import parse_executors_yaml, sync_executors


@dataclass(frozen=True, slots=True)
class AdminResult:
    commands: tuple[Event, ...]


def _reply(chat_id: int, text: str) -> CmdSendMessage:
    return CmdSendMessage(chat_id=chat_id, text=text, parse_mode="HTML")


@dataclass(slots=True)
class RenameCustomer:
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessage) -> AdminResult:
        if not await self.processed.try_mark(event.event_id):
            return AdminResult(commands=())
        try:
            args = parse_rename_customer(event.text or "")
        except AdminCommandParseError:
            return AdminResult(commands=(_reply(event.chat_id, usage_text("/rename_customer")),))
        renamed = await self.customers.rename(args.chat_id, args.new_title)
        if renamed is None:
            return AdminResult(
                commands=(_reply(event.chat_id, customer_not_found_text(args.chat_id)),)
            )
        return AdminResult(
            commands=(_reply(event.chat_id, rename_success_text(args.chat_id, args.new_title)),)
        )


@dataclass(slots=True)
class DeactivateCustomer:
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessage) -> AdminResult:
        if not await self.processed.try_mark(event.event_id):
            return AdminResult(commands=())
        try:
            args = parse_chat_id_only(event.text or "", "/deactivate_customer")
        except AdminCommandParseError:
            return AdminResult(
                commands=(_reply(event.chat_id, usage_text("/deactivate_customer")),)
            )
        updated = await self.customers.set_active(args.chat_id, active=False)
        if updated is None:
            return AdminResult(
                commands=(_reply(event.chat_id, customer_not_found_text(args.chat_id)),)
            )
        return AdminResult(commands=(_reply(event.chat_id, deactivated_text(args.chat_id)),))


@dataclass(slots=True)
class ActivateCustomer:
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessage) -> AdminResult:
        if not await self.processed.try_mark(event.event_id):
            return AdminResult(commands=())
        try:
            args = parse_chat_id_only(event.text or "", "/activate_customer")
        except AdminCommandParseError:
            return AdminResult(commands=(_reply(event.chat_id, usage_text("/activate_customer")),))
        updated = await self.customers.set_active(args.chat_id, active=True)
        if updated is None:
            return AdminResult(
                commands=(_reply(event.chat_id, customer_not_found_text(args.chat_id)),)
            )
        return AdminResult(commands=(_reply(event.chat_id, activated_text(args.chat_id)),))


@dataclass(slots=True)
class ReloadExecutors:
    executors: ExecutorsRepository
    processed: ProcessedEventsRepository
    config_path: str

    async def execute(self, event: TgMessage) -> AdminResult:
        if not await self.processed.try_mark(event.event_id):
            return AdminResult(commands=())
        p = Path(self.config_path)
        if not p.exists():  # noqa: ASYNC240
            return AdminResult(
                commands=(_reply(event.chat_id, reload_executors_missing_text(self.config_path)),)
            )
        content = p.read_text(encoding="utf-8")  # noqa: ASYNC240
        items = parse_executors_yaml(content)
        await sync_executors(self.executors, items)
        return AdminResult(commands=(_reply(event.chat_id, reload_executors_text(len(items))),))


@dataclass(slots=True)
class ListCustomers:
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessage) -> AdminResult:
        if not await self.processed.try_mark(event.event_id):
            return AdminResult(commands=())
        rows = await self.customers.list_all()
        if not rows:
            return AdminResult(commands=(_reply(event.chat_id, list_customers_empty_text()),))
        items = [(c.telegram_chat_id, c.title, c.is_active) for c in rows]
        return AdminResult(commands=(_reply(event.chat_id, list_customers_text(items)),))
