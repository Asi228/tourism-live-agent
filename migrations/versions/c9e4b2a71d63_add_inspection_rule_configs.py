"""add configurable inspection rules

Revision ID: c9e4b2a71d63
Revises: b6d2f4a91c37
Create Date: 2026-10-23 20:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e4b2a71d63"
down_revision: Union[str, Sequence[str], None] = "b6d2f4a91c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RULES = [
    ("SESSION_HAS_ITEMS", "场次选品完整性", "场次至少包含一个可售 SKU", "blocker", {}),
    ("SKU_ACTIVE", "商品与 SKU 状态", "商品主档与 SKU 必须处于启用状态", "blocker", {}),
    ("INVENTORY_SUFFICIENT", "可售库存充足", "可售库存必须覆盖本场计划销量", "blocker", {}),
    ("INVENTORY_FRESH", "库存快照时效", "库存数据不能早于配置的最大时效", "warning", {"max_age_hours": 24}),
    ("PROMOTION_ACTIVE", "开播时活动有效", "计划开播时间应存在有效促销活动", "warning", {}),
    ("PRICE_MATCH", "计划到手价一致", "活动计算价必须与运营计划到手价一致", "blocker", {}),
    ("PRODUCT_MANUAL_VALID", "商品说明资料有效", "开播时必须存在有效商品说明资料", "blocker", {}),
    ("CLAIM_EVIDENCE_VALID", "宣传宣称依据有效", "营销宣称应有质检、授权或证明材料", "warning", {}),
]


def upgrade() -> None:
    table = op.create_table(
        "inspection_rule_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("rule_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_code"),
    )
    op.create_index("ix_inspection_rule_configs_rule_code", "inspection_rule_configs", ["rule_code"], unique=True)
    op.create_index("ix_inspection_rule_configs_enabled", "inspection_rule_configs", ["enabled"], unique=False)
    op.bulk_insert(
        table,
        [
            {
                "rule_code": code,
                "rule_name": name,
                "description": description,
                "enabled": True,
                "severity": severity,
                "parameters": parameters,
                "version": 1,
            }
            for code, name, description, severity, parameters in RULES
        ],
    )


def downgrade() -> None:
    op.drop_table("inspection_rule_configs")
