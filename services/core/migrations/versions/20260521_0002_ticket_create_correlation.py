"""ticket: nullable topic_id + create_correlation_id

Spec 002 — create ticket. Двухфазное создание: фаза 1 пишет тикет в БД, ещё
до создания форум-топика; topic_id заполнится фазой 2 из events.tg.topic_created
по correlation_id.

Revision ID: 0002_ticket_create_correlation
Revises: 0001_initial_core
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ticket_create_correlation"
down_revision: str | Sequence[str] | None = "0001_initial_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # tickets.topic_id → nullable
    op.alter_column("tickets", "topic_id", existing_type=sa.Integer(), nullable=True)
    # tickets.create_correlation_id UUID UNIQUE NULL
    op.add_column(
        "tickets",
        sa.Column("create_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_tickets_create_correlation",
        "tickets",
        ["create_correlation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tickets_create_correlation", "tickets", type_="unique")
    op.drop_column("tickets", "create_correlation_id")
    op.alter_column("tickets", "topic_id", existing_type=sa.Integer(), nullable=False)
