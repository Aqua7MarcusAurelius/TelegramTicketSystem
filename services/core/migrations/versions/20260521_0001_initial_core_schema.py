"""initial core schema

Создаёт таблицы core (SPEC §10.2):
- ticket_status ENUM
- customers, executors, tickets, ticket_events
- fsm_state
- core_processed_events

Revision ID: 0001_initial_core
Revises:
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_core"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TICKET_STATUS_ENUM = "ticket_status"
TICKET_STATUS_VALUES = ("new", "in_progress", "closed")


def upgrade() -> None:
    # ENUM как explicit type — нужен для CREATE TABLE и в дальнейшем для ALTER.
    ticket_status = postgresql.ENUM(
        *TICKET_STATUS_VALUES,
        name=TICKET_STATUS_ENUM,
        create_type=False,
    )
    op.execute(
        f"CREATE TYPE {TICKET_STATUS_ENUM} AS ENUM ("
        + ", ".join(f"'{v}'" for v in TICKET_STATUS_VALUES)
        + ")"
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("menu_message_id", sa.Integer(), nullable=True),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "executors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_lead",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id"),
            nullable=False,
        ),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("header_message_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            ticket_status,
            nullable=False,
            server_default="new",
        ),
        sa.Column(
            "assignee_id",
            sa.Integer(),
            sa.ForeignKey("executors.id"),
            nullable=True,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("in_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("customer_id", "topic_id", name="uq_tickets_customer_topic"),
    )
    op.create_index("idx_tickets_status", "tickets", ["status"])
    op.create_index(
        "idx_tickets_assignee",
        "tickets",
        ["assignee_id"],
        postgresql_where=sa.text("status != 'closed'"),
    )
    op.create_index("idx_tickets_customer", "tickets", ["customer_id"])
    op.create_index("idx_tickets_created_by", "tickets", ["created_by_user_id"])

    op.create_table(
        "ticket_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("tickets.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_ticket_events_ticket", "ticket_events", ["ticket_id"])

    op.create_table(
        "fsm_state",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_fsm_expires",
        "fsm_state",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    op.create_table(
        "core_processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("core_processed_events")
    op.drop_index("idx_fsm_expires", table_name="fsm_state")
    op.drop_table("fsm_state")
    op.drop_index("idx_ticket_events_ticket", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("idx_tickets_created_by", table_name="tickets")
    op.drop_index("idx_tickets_customer", table_name="tickets")
    op.drop_index("idx_tickets_assignee", table_name="tickets")
    op.drop_index("idx_tickets_status", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("executors")
    op.drop_table("customers")
    op.execute(f"DROP TYPE {TICKET_STATUS_ENUM}")
