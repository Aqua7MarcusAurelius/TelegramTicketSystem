"""Declarative Base для всех моделей core."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс всех моделей core.

    Свои таблицы — здесь. Чужие (notifications, sheets-sync) трогать нельзя —
    см. SPEC §10.1.
    """
