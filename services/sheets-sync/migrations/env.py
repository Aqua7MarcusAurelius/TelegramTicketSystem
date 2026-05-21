"""Alembic env для sheets-sync. Шаблон тот же, что и в core."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from sheets_sync.repository import models  # noqa: E402, F401
from sheets_sync.repository.base import Base  # noqa: E402

target_metadata = Base.metadata

# Свои таблицы — фильтр для autogenerate/check, чтобы не дергать чужие таблицы
# core/notifications, которые живут в той же БД.
_OWN_TABLES = {"sheets_sync_state", "sheets_sync_processed_events"}


def _include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _OWN_TABLES
    if type_ == "index" and obj is not None and obj.table is not None:
        return obj.table.name in _OWN_TABLES
    return True


def _get_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get("POSTGRES_DSN")
    if not url:
        raise RuntimeError("POSTGRES_DSN is not set and -x url=... not provided")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="alembic_version_sheets_sync",
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table="alembic_version_sheets_sync",
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
