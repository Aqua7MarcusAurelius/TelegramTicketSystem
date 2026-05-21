"""Доменные события тикетов (``events.ticket.*``).

См. docs/SPEC.md §9.3.
"""

from __future__ import annotations

from datetime import datetime

from shared.events.base import Event


class TicketCreated(Event):
    """``events.ticket.created`` — тикет создан заказчиком."""

    ticket_id: int
    customer_id: int
    customer_chat_id: int
    topic_id: int
    title: str
    description: str
    created_by_user_id: int
    created_at: datetime


class TicketAssigned(Event):
    """``events.ticket.assigned`` — тикет переведён в работу.

    ``assigned_by_user_id`` может равняться ``assignee_user_id`` при self-pickup.
    Различие нужно для аудита «кто назначил» (см. SPEC §8.2).
    """

    ticket_id: int
    assignee_user_id: int
    assigned_by_user_id: int
    assigned_at: datetime


class TicketClosed(Event):
    """``events.ticket.closed`` — заказчик закрыл тикет."""

    ticket_id: int
    closed_by_user_id: int
    closed_at: datetime
