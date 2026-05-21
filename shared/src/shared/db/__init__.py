"""Базовые утилиты для SQLAlchemy (async).

См. SPEC §10.1 — владение таблицами строго по сервисам, чужие не трогаем.
"""

from shared.db.session import build_engine, build_session_factory

__all__ = ["build_engine", "build_session_factory"]
