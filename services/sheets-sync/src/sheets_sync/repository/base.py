"""Declarative Base для моделей sheets-sync."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс моделей sheets-sync.

    SPEC §10.1: сервис описывает только свои таблицы — ``sheets_sync_state`` и
    ``sheets_sync_processed_events``. Чужие данные (tickets, customers и т.п.)
    приходят через шину.
    """
