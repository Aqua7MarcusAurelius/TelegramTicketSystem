"""initial sheets-sync schema

Создаёт:
- ``sheets_sync_state`` — маппинг ticket_id → row номер в Sheets
- ``sheets_sync_processed_events`` — идемпотентность сервиса

См. SPEC §10.2, §11.5.

Revision ID: 0001_initial_sheets_sync
Revises:
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_sheets_sync"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sheets_sync_state",
        sa.Column("ticket_id", sa.Integer(), primary_key=True),
        sa.Column("sheet_row", sa.Integer(), nullable=False),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_event_id", postgresql.UUID(as_uuid=True), nullable=False),
    )

    op.create_table(
        "sheets_sync_processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("sheets_sync_processed_events")
    op.drop_table("sheets_sync_state")
