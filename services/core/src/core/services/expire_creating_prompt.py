"""Use-case: тайм-аут состояния ``creating_prompt`` (SPEC §7.2).

Логика — найти все ``fsm_state`` с ``state='creating_prompt'`` и истёкшим
``expires_at``; для каждой — сбросить FSM в ``main``, закрыть General и
обновить меню с тостом «⏱ Время вышло». Шапка тикета здесь не страдает —
тикет ещё не создан в этом сценарии.

Use-case возвращает список команд для публикации. Циклом-обёрткой управляет
:mod:`core.main`.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.events import (
    CmdCloseGeneralForumTopic,
    CmdEditMessageText,
    Event,
)

from core.domain.menu import MenuState
from core.domain.menu_render import render_main
from core.repository.customers import CustomersRepository
from core.repository.fsm import FsmStateRepository


@dataclass(slots=True)
class ExpireCreatingPrompt:
    fsm: FsmStateRepository
    customers: CustomersRepository

    async def run_once(self) -> list[Event]:
        """Один проход чистки. Возвращает команды для публикации."""

        expired = await self.fsm.list_expired(limit=100)
        commands: list[Event] = []
        for row in expired:
            if row.state != MenuState.CREATING_PROMPT.value:
                # Истёк, но не наш случай — просто удаляем запись и едем дальше.
                await self.fsm.clear(row.user_id, row.chat_id)
                continue

            customer = await self.customers.get_by_chat(row.chat_id)
            await self.fsm.clear(row.user_id, row.chat_id)
            if customer is None or customer.menu_message_id is None:
                # Заказчик пропал или меню не создано — не паникуем, FSM уже сбросили.
                continue

            screen = render_main()
            commands.append(CmdCloseGeneralForumTopic(chat_id=customer.telegram_chat_id))
            commands.append(
                CmdEditMessageText(
                    chat_id=customer.telegram_chat_id,
                    message_id=customer.menu_message_id,
                    text=screen.text + "\n\n⏱ Время вышло",
                    reply_markup=screen.reply_markup,
                    parse_mode="HTML",
                )
            )

        return commands
