"""Доменные таблицы core. DDL соответствует SPEC §10.2.

Эта миграция — инициальная схема: содержит все таблицы core'а. Логика INSERT
для tickets/ticket_events приходит позже (spec 002+), но DDL живёт здесь
с самого начала, чтобы spec'ы 001..004 могли деплоиться единым миграционным
циклом.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.repository.base import Base


class TicketStatus(enum.StrEnum):
    """SPEC §6 — статусы тикета. Соответствует PG-ENUM ``ticket_status``."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class Customer(Base):
    """Группа заказчика. SPEC §10.2."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    menu_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    tickets: Mapped[list[Ticket]] = relationship(back_populates="customer", lazy="raise")


class Executor(Base):
    """Исполнитель. Подтягивается из ``executors.yaml`` (SPEC §3.4)."""

    __tablename__ = "executors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_lead: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Ticket(Base):
    """Тикет. SPEC §10.2.

    Создаётся в spec 002, но DDL присутствует с initial-миграции, чтобы spec 001
    мог делать READ по `created_by_user_id` для отображения «📋 Мои тикеты».
    """

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("customer_id", "topic_id", name="uq_tickets_customer_topic"),
        Index("idx_tickets_status", "status"),
        Index(
            "idx_tickets_assignee",
            "assignee_id",
            postgresql_where="status != 'closed'",
        ),
        Index("idx_tickets_customer", "customer_id"),
        Index("idx_tickets_created_by", "created_by_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    header_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        server_default=TicketStatus.NEW.value,
    )
    assignee_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("executors.id"), nullable=True
    )
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="tickets", lazy="raise")


class TicketEvent(Base):
    """Лог переходов и значимых событий по тикету. SPEC §10.2."""

    __tablename__ = "ticket_events"
    __table_args__ = (Index("idx_ticket_events_ticket", "ticket_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FsmState(Base):
    """Состояние single-message UI per (user_id, chat_id). SPEC §7, §10.2."""

    __tablename__ = "fsm_state"
    __table_args__ = (
        Index("idx_fsm_expires", "expires_at", postgresql_where="expires_at IS NOT NULL"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedEvent(Base):
    """Идемпотентность шины. SPEC §9.1, §10.2."""

    __tablename__ = "core_processed_events"

    event_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
