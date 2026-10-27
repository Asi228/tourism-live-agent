"""add planned price column comment

Revision ID: d7e1a5c83f29
Revises: c4b9e2a71d60
Create Date: 2026-10-21 22:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e1a5c83f29"
down_revision: Union[str, Sequence[str], None] = "c4b9e2a71d60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "live_session_items",
        "planned_unit_price",
        existing_type=sa.Numeric(precision=12, scale=2),
        existing_nullable=True,
        comment="运营计划到手单价；用于价格巡检",
    )


def downgrade() -> None:
    op.alter_column(
        "live_session_items",
        "planned_unit_price",
        existing_type=sa.Numeric(precision=12, scale=2),
        existing_nullable=True,
        comment=None,
    )
