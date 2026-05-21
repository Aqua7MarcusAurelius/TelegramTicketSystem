"""customer: menu_correlation_id

Spec 005 — onboarding группы заказчика. Чтобы заполнить
``customers.menu_message_id`` асинхронно (по ответному ``events.tg.message_sent``),
храним correlation UUID, выставленный в команде ``cmd.tg.send_message``.

Revision ID: 0004_customer_menu_correlation
Revises: 0003_ticket_inbox_correlation
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_customer_menu_correlation"
down_revision: str | Sequence[str] | None = "0003_ticket_inbox_correlation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("menu_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_customers_menu_correlation",
        "customers",
        ["menu_correlation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_customers_menu_correlation", "customers", type_="unique")
    op.drop_column("customers", "menu_correlation_id")
