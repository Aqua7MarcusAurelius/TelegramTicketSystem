"""Use-case'ы онбординга группы заказчика. SPEC §3.5, spec 005.

Триггеры:
- ``events.tg.bot_membership_changed`` (new_status=administrator)
- команда ``/setup`` в любой группе (запускается из handlers/tg_message.py)
- callback ``setup_recheck`` после исправления прав (handlers/tg_callback.py)

Все три приводят к одному и тому же :class:`OnboardCustomer.execute`.

Фаза 2 (сохранение message_id меню) делает :class:`HandleMenuMessageSent` —
подписан на ``events.tg.message_sent`` и матчит по ``menu_correlation_id``.

Также есть :class:`HandleBotKicked` — отдельный путь для new_status=left/kicked:
просто пишем WARNING, ``is_active`` не меняем (SPEC §3.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import structlog
from shared.events import (
    CmdCloseGeneralForumTopic,
    CmdEditGeneralForumTopic,
    CmdPinMessage,
    CmdSendMessage,
    Event,
    TgBotMembershipChanged,
    TgMessageSent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.menu_render import render_main
from core.domain.onboarding import (
    GENERAL_MENU_NAME,
    MissingRights,
    added_to_group_hint_text,
    already_registered_text,
    missing_rights_keyboard,
    missing_rights_text,
    not_a_forum_text,
)
from core.repository.customers import CustomersRepository
from core.repository.processed_events import ProcessedEventsRepository

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OnboardResult:
    """Команды для публикации.

    ``customer_created`` показывает, что мы реально завели нового заказчика —
    нужно для тестов и логов.
    """

    commands: tuple[Event, ...]
    customer_created: bool = False


@dataclass(frozen=True, slots=True)
class OnboardSkipped:
    reason: str
    commands: tuple[Event, ...] = ()


@dataclass(slots=True)
class OnboardCustomer:
    session: AsyncSession
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def from_membership_event(
        self, event: TgBotMembershipChanged
    ) -> OnboardResult | OnboardSkipped:
        """Триггер №1: бот стал админом.

        Авто-onboarding **отключён** (см. update spec 005): бот не различает
        customer-группу от team-группы по факту добавления. Вместо регистрации
        мы один раз отправляем подсказку, дальше пользователь выбирает явной
        командой ``/setup`` (customer) или ``/setup_team_group`` (team).
        """

        if event.new_status != "administrator":
            return OnboardSkipped(reason="not_administrator")
        if not await self.processed.try_mark(event.event_id):
            return OnboardSkipped(reason="already_processed")

        # Если эта группа уже зарегистрирована как customer — повторно регистрация
        # не нужна, но и сообщение тоже не шлём (это просто обновление прав бота).
        existing = await self.customers.get_by_chat(event.chat_id)
        if existing is not None and existing.menu_message_id is not None:
            return OnboardSkipped(reason="already_onboarded")

        return OnboardResult(
            commands=(
                CmdSendMessage(
                    chat_id=event.chat_id,
                    text=added_to_group_hint_text(),
                    parse_mode="HTML",
                ),
            ),
            customer_created=False,
        )

    async def from_setup_command(
        self,
        *,
        chat_id: int,
        chat_title: str,
        is_forum: bool,
        rights: MissingRights,
    ) -> OnboardResult | OnboardSkipped:
        """Триггер №2: исполнитель написал ``/setup`` в группе.

        Идемпотентность по event_id обеспечивает вызывающий handler
        (он сам делает try_mark на TgMessage). Здесь — чистая логика.
        """

        return await self._run(
            chat_id=chat_id,
            chat_title=chat_title,
            is_forum=is_forum,
            rights=rights,
        )

    async def _run(
        self,
        *,
        chat_id: int,
        chat_title: str,
        is_forum: bool,
        rights: MissingRights,
    ) -> OnboardResult | OnboardSkipped:
        # 1) Уже подключено?
        existing = await self.customers.get_by_chat(chat_id)
        if existing is not None and existing.menu_message_id is not None:
            return OnboardResult(
                commands=(
                    CmdSendMessage(
                        chat_id=chat_id,
                        text=already_registered_text(existing.title),
                        parse_mode="HTML",
                    ),
                )
            )

        # 2) Не форум?
        if not is_forum:
            return OnboardResult(
                commands=(
                    CmdSendMessage(chat_id=chat_id, text=not_a_forum_text(), parse_mode="HTML"),
                )
            )

        # 3) Не хватает прав?
        if not rights.all_present:
            return OnboardResult(
                commands=(
                    CmdSendMessage(
                        chat_id=chat_id,
                        text=missing_rights_text(rights),
                        reply_markup=missing_rights_keyboard(),
                        parse_mode="HTML",
                    ),
                )
            )

        # 4) Всё ок — заводим заказчика и отправляем меню.
        correlation_id = uuid4()
        if existing is None:
            await self.customers.create(
                telegram_chat_id=chat_id,
                title=chat_title,
                menu_correlation_id=correlation_id,
            )
            created = True
        else:
            # Заказчик уже есть, но menu_message_id отсутствует — заполним по новой
            # (например, после рестарта стека или если предыдущий retry не дошёл).
            existing.menu_correlation_id = correlation_id
            created = False

        screen = render_main()
        commands: tuple[Event, ...] = (
            CmdEditGeneralForumTopic(chat_id=chat_id, name=GENERAL_MENU_NAME),
            CmdSendMessage(
                correlation_id=correlation_id,
                chat_id=chat_id,
                text=screen.text,
                reply_markup=screen.reply_markup,
                parse_mode="HTML",
            ),
            CmdCloseGeneralForumTopic(chat_id=chat_id),
        )
        return OnboardResult(commands=commands, customer_created=created)


# ---------------------------------------------------------------------
# Фаза 2 — пришёл message_sent для меню
# ---------------------------------------------------------------------


@dataclass(slots=True)
class HandleMenuMessageSent:
    customers: CustomersRepository
    processed: ProcessedEventsRepository

    async def execute(self, event: TgMessageSent) -> OnboardResult | OnboardSkipped:
        if event.correlation_id is None:
            return OnboardSkipped(reason="no_correlation")
        if not await self.processed.try_mark(event.event_id):
            return OnboardSkipped(reason="already_processed")

        customer = await self.customers.get_by_menu_correlation(event.correlation_id)
        if customer is None:
            return OnboardSkipped(reason="unknown_menu_correlation")

        await self.customers.set_menu_message_id(customer.id, event.message_id)
        return OnboardResult(
            commands=(
                CmdPinMessage(
                    chat_id=event.chat_id,
                    message_id=event.message_id,
                    disable_notification=True,
                ),
            )
        )


# ---------------------------------------------------------------------
# Бота кикнули
# ---------------------------------------------------------------------


def log_bot_kicked(event: TgBotMembershipChanged) -> None:
    """SPEC §3.5: kick → WARNING, is_active не трогаем (мог быть случайностью)."""

    log.warning(
        "bot_kicked_from_customer_group",
        chat_id=event.chat_id,
        chat_title=event.chat_title,
        new_status=event.new_status,
        actor_user_id=event.actor_user_id,
    )
