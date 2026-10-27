"""live commerce business skeleton

Revision ID: e2a7c4d91f30
Revises: d9e4f1a2b3c5
Create Date: 2026-10-21 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2a7c4d91f30"
down_revision: Union[str, Sequence[str], None] = "d9e4f1a2b3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_name", "products", ["name"])
    op.create_index("ix_products_brand", "products", ["brand"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_status", "products", ["status"])

    op.create_table(
        "product_skus",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=64), nullable=False),
        sa.Column("specification", sa.String(length=128), nullable=False),
        sa.Column("list_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_skus_product_id", "product_skus", ["product_id"])
    op.create_index("ix_product_skus_sku_code", "product_skus", ["sku_code"], unique=True)
    op.create_index("ix_product_skus_status", "product_skus", ["status"])

    op.create_table(
        "live_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_live_sessions_starts_at", "live_sessions", ["starts_at"])
    op.create_index("ix_live_sessions_status", "live_sessions", ["status"])
    op.create_index("ix_live_sessions_owner_id", "live_sessions", ["owner_id"])

    op.create_table(
        "live_session_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("planned_sales", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["live_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sku_id", name="uq_live_session_item_sku"),
    )
    op.create_index("ix_live_session_items_session_id", "live_session_items", ["session_id"])
    op.create_index("ix_live_session_items_sku_id", "live_session_items", ["sku_id"])

    op.create_table(
        "preparation_packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["live_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "version", name="uq_preparation_package_version"),
    )
    op.create_index("ix_preparation_packages_session_id", "preparation_packages", ["session_id"])
    op.create_index("ix_preparation_packages_status", "preparation_packages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_preparation_packages_status", table_name="preparation_packages")
    op.drop_index("ix_preparation_packages_session_id", table_name="preparation_packages")
    op.drop_table("preparation_packages")
    op.drop_index("ix_live_session_items_sku_id", table_name="live_session_items")
    op.drop_index("ix_live_session_items_session_id", table_name="live_session_items")
    op.drop_table("live_session_items")
    op.drop_index("ix_live_sessions_owner_id", table_name="live_sessions")
    op.drop_index("ix_live_sessions_status", table_name="live_sessions")
    op.drop_index("ix_live_sessions_starts_at", table_name="live_sessions")
    op.drop_table("live_sessions")
    op.drop_index("ix_product_skus_status", table_name="product_skus")
    op.drop_index("ix_product_skus_sku_code", table_name="product_skus")
    op.drop_index("ix_product_skus_product_id", table_name="product_skus")
    op.drop_table("product_skus")
    op.drop_index("ix_products_status", table_name="products")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_index("ix_products_name", table_name="products")
    op.drop_table("products")

