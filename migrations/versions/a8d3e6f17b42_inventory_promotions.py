"""inventory snapshots and promotions

Revision ID: a8d3e6f17b42
Revises: f5c8a1b42d67
Create Date: 2026-10-21 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8d3e6f17b42"
down_revision: Union[str, Sequence[str], None] = "f5c8a1b42d67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_snapshots_sku_id", "inventory_snapshots", ["sku_id"])
    op.create_index("ix_inventory_snapshots_captured_at", "inventory_snapshots", ["captured_at"])

    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("promotion_type", sa.String(length=32), nullable=False),
        sa.Column("fixed_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("threshold_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("discount_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stackable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promotions_promotion_type", "promotions", ["promotion_type"])
    op.create_index("ix_promotions_status", "promotions", ["status"])
    op.create_index("ix_promotions_starts_at", "promotions", ["starts_at"])
    op.create_index("ix_promotions_ends_at", "promotions", ["ends_at"])

    op.create_table(
        "promotion_skus",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("promotion_id", "sku_id", name="uq_promotion_sku"),
    )
    op.create_index("ix_promotion_skus_promotion_id", "promotion_skus", ["promotion_id"])
    op.create_index("ix_promotion_skus_sku_id", "promotion_skus", ["sku_id"])


def downgrade() -> None:
    op.drop_index("ix_promotion_skus_sku_id", table_name="promotion_skus")
    op.drop_index("ix_promotion_skus_promotion_id", table_name="promotion_skus")
    op.drop_table("promotion_skus")
    op.drop_index("ix_promotions_ends_at", table_name="promotions")
    op.drop_index("ix_promotions_starts_at", table_name="promotions")
    op.drop_index("ix_promotions_status", table_name="promotions")
    op.drop_index("ix_promotions_promotion_type", table_name="promotions")
    op.drop_table("promotions")
    op.drop_index("ix_inventory_snapshots_captured_at", table_name="inventory_snapshots")
    op.drop_index("ix_inventory_snapshots_sku_id", table_name="inventory_snapshots")
    op.drop_table("inventory_snapshots")

