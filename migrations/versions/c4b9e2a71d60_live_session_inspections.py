"""live session inspection engine

Revision ID: c4b9e2a71d60
Revises: a8d3e6f17b42
Create Date: 2026-10-21 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4b9e2a71d60"
down_revision: Union[str, Sequence[str], None] = "a8d3e6f17b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "live_session_items",
        sa.Column(
            "planned_unit_price",
            sa.Numeric(precision=12, scale=2),
            nullable=True,
            comment="运营计划到手单价；用于价格巡检",
        ),
    )

    op.create_table(
        "inspection_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("conclusion", sa.String(length=16), nullable=True),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocker_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("passed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rule_version", sa.String(length=32), server_default="2026.10-v1", nullable=False),
        sa.Column("checked_by", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["checked_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["live_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspection_batches_session_id", "inspection_batches", ["session_id"])
    op.create_index("ix_inspection_batches_status", "inspection_batches", ["status"])
    op.create_index("ix_inspection_batches_conclusion", "inspection_batches", ["conclusion"])

    op.create_table(
        "inspection_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("rule_name", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=512), nullable=False),
        sa.Column("suggestion", sa.String(length=512), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["inspection_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspection_results_batch_id", "inspection_results", ["batch_id"])
    op.create_index("ix_inspection_results_rule_code", "inspection_results", ["rule_code"])
    op.create_index("ix_inspection_results_severity", "inspection_results", ["severity"])
    op.create_index("ix_inspection_results_status", "inspection_results", ["status"])
    op.create_index("ix_inspection_results_sku_id", "inspection_results", ["sku_id"])


def downgrade() -> None:
    op.drop_index("ix_inspection_results_sku_id", table_name="inspection_results")
    op.drop_index("ix_inspection_results_status", table_name="inspection_results")
    op.drop_index("ix_inspection_results_severity", table_name="inspection_results")
    op.drop_index("ix_inspection_results_rule_code", table_name="inspection_results")
    op.drop_index("ix_inspection_results_batch_id", table_name="inspection_results")
    op.drop_table("inspection_results")
    op.drop_index("ix_inspection_batches_conclusion", table_name="inspection_batches")
    op.drop_index("ix_inspection_batches_status", table_name="inspection_batches")
    op.drop_index("ix_inspection_batches_session_id", table_name="inspection_batches")
    op.drop_table("inspection_batches")
    op.drop_column("live_session_items", "planned_unit_price")
