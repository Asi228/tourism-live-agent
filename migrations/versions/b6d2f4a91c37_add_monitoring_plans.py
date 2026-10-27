"""add scheduled live monitoring plans

Revision ID: b6d2f4a91c37
Revises: a1f7c9d42e86
Create Date: 2026-10-23 12:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6d2f4a91c37"
down_revision: Union[str, Sequence[str], None] = "a1f7c9d42e86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitoring_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_task_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["last_task_id"], ["rpa_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["live_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_plans_enabled", "monitoring_plans", ["enabled"], unique=False)
    op.create_index("ix_monitoring_plans_next_run_at", "monitoring_plans", ["next_run_at"], unique=False)
    op.create_index("ix_monitoring_plans_session_id", "monitoring_plans", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_table("monitoring_plans")
