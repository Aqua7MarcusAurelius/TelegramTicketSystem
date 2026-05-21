"""ticket: inbox correlation (cmd.tg.send_message в командную группу)

Spec 003 — take ticket. Чтобы редактировать карточку «🆕 Входящие» при
назначении исполнителя, core должен помнить message_id этой карточки.
Связь — через correlation_id, который core ставит в cmd.tg.send_message
и получает обратно в events.tg.message_sent.

Revision ID: 0003_ticket_inbox_correlation
Revises: 0002_ticket_create_correlation
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ticket_inbox_correlation"
down_revision: str | Sequence[str] | None = "0002_ticket_create_correlation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("inbox_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("inbox_message_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_tickets_inbox_correlation",
        "tickets",
        ["inbox_correlation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tickets_inbox_correlation", "tickets", type_="unique")
    op.drop_column("tickets", "inbox_message_id")
    op.drop_column("tickets", "inbox_correlation_id")
