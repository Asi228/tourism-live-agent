"""document commerce metadata

Revision ID: f5c8a1b42d67
Revises: e2a7c4d91f30
Create Date: 2026-10-21 20:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5c8a1b42d67"
down_revision: Union[str, Sequence[str], None] = "e2a7c4d91f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("product_id", sa.Integer(), nullable=True, comment="关联商品"))
        batch.add_column(sa.Column("sku_id", sa.Integer(), nullable=True, comment="关联 SKU"))
        batch.add_column(
            sa.Column(
                "document_type", sa.String(length=32),
                server_default="general", nullable=False, comment="业务资料类型",
            )
        )
        batch.add_column(
            sa.Column(
                "version", sa.String(length=32),
                server_default="v1", nullable=False, comment="业务资料版本",
            )
        )
        batch.add_column(sa.Column("effective_from", sa.DateTime(), nullable=True, comment="生效时间"))
        batch.add_column(sa.Column("effective_to", sa.DateTime(), nullable=True, comment="失效时间"))
        batch.create_foreign_key(
            "fk_documents_product_id_products", "products", ["product_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_documents_sku_id_product_skus", "product_skus", ["sku_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_documents_product_id", ["product_id"])
        batch.create_index("ix_documents_sku_id", ["sku_id"])
        batch.create_index("ix_documents_document_type", ["document_type"])
        batch.create_index("ix_documents_effective_from", ["effective_from"])
        batch.create_index("ix_documents_effective_to", ["effective_to"])


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_effective_to")
        batch.drop_index("ix_documents_effective_from")
        batch.drop_index("ix_documents_document_type")
        batch.drop_index("ix_documents_sku_id")
        batch.drop_index("ix_documents_product_id")
        batch.drop_constraint("fk_documents_sku_id_product_skus", type_="foreignkey")
        batch.drop_constraint("fk_documents_product_id_products", type_="foreignkey")
        batch.drop_column("effective_to")
        batch.drop_column("effective_from")
        batch.drop_column("version")
        batch.drop_column("document_type")
        batch.drop_column("sku_id")
        batch.drop_column("product_id")

