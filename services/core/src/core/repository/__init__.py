"""Слой доступа к БД для core.

Modules:
- ``base`` — declarative Base.
- ``models`` — SQLAlchemy-модели всех доменных таблиц.
- ``fsm`` — FsmStateRepository (single-message UI state).
- ``customers`` — CustomersRepository.
- ``processed_events`` — ProcessedEventsRepository (идемпотентность).
"""
