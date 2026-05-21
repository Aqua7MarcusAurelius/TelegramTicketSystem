"""team_group_topic_setup

Spec 006 — onboarding командной группы. На каждый из 4 запрошенных топиков
(`incoming`, `digest`, `logs`, `escalations`) создаём строку с UUID, который
ставится в correlation_id команды ``cmd.tg.create_forum_topic``. Ответ
``events.tg.topic_created`` приходит с тем же correlation_id — заполняем
``topic_id``. Когда все 4 строки конкретной группы получили ``topic_id``,
публикуется env-блок для пользователя.

Revision ID: 0005_team_group_topic_setup
Revises: 0004_customer_menu_correlation
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_team_group_topic_setup"
down_revision: str | Sequence[str] | None = "0004_customer_menu_correlation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_group_topic_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),  # incoming/digest/logs/escalations
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("chat_id", "role", name="uq_team_group_chat_role"),
    )
    op.create_index("idx_team_group_chat", "team_group_topic_setup", ["chat_id"])


def downgrade() -> None:
    op.drop_index("idx_team_group_chat", table_name="team_group_topic_setup")
    op.drop_table("team_group_topic_setup")
