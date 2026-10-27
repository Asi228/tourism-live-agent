"""link approvals to the inspection risk that triggered them

Revision ID: a1f7c9d42e86
Revises: f3a8c2e91b70
Create Date: 2026-10-22 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f7c9d42e86"
down_revision: Union[str, Sequence[str], None] = "f3a8c2e91b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rpa_approvals",
        sa.Column(
            "source_inspection_result_id",
            sa.Integer(),
            nullable=True,
            comment="触发本申请的巡检风险",
        ),
    )
    op.add_column(
        "rpa_approvals",
        sa.Column(
            "source_snapshot",
            sa.JSON(),
            nullable=True,
            comment="申请时的风险与证据快照",
        ),
    )
    op.create_index(
        "ix_rpa_approvals_source_inspection_result_id",
        "rpa_approvals",
        ["source_inspection_result_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_rpa_approvals_source_inspection_result_id",
        "rpa_approvals",
        "inspection_results",
        ["source_inspection_result_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_rpa_approvals_source_inspection_result_id",
        "rpa_approvals",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_rpa_approvals_source_inspection_result_id",
        table_name="rpa_approvals",
    )
    op.drop_column("rpa_approvals", "source_snapshot")
    op.drop_column("rpa_approvals", "source_inspection_result_id")
