"""SQLAlchemy-модели sheets-sync. SPEC §10.2."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheets_sync.repository.base import Base


class SheetsSyncState(Base):
    """Маппинг ticket_id → строка в Google Sheets."""

    __tablename__ = "sheets_sync_state"

    ticket_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row: Mapped[int] = mapped_column(Integer, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_event_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False)


class ProcessedEvent(Base):
    """Идемпотентность шины — отдельная таблица сервиса (SPEC §9.1)."""

    __tablename__ = "sheets_sync_processed_events"

    event_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
