"""add rpa_approvals (high-risk operation approval)

Revision ID: f3a8c2e91b70
Revises: e9c2b7d14a58
Create Date: 2026-10-21 22:45:00.000000

审批单 = 一次可执行的授权：绑定目标 SKU + 预期值 + 幂等键。
审批通过后生成的 RPA 修复任务用同一幂等键做唯一约束 —— 重复审批 / worker 重试
都不会产生第二次改价（这是阶段四"可控写入"的核心约束）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a8c2e91b70'
down_revision: Union[str, Sequence[str], None] = 'e9c2b7d14a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rpa_approvals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.String(length=32), nullable=False),
        sa.Column('sku_id', sa.Integer(), nullable=False),
        sa.Column('expected_value', sa.JSON(), nullable=False, comment="目标状态（执行后按它验证），如 {'live_price': '99.00'}"),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=512), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('decision_note', sa.String(length=512), nullable=True, comment='批准备注 / 驳回理由'),
        sa.Column('task_id', sa.Integer(), nullable=True, comment='通过后生成的执行任务'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sku_id'], ['product_skus.id'], ),
        sa.ForeignKeyConstraint(['task_id'], ['rpa_tasks.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_rpa_approvals_idempotency_key'),
    )
    op.create_index(op.f('ix_rpa_approvals_session_id'), 'rpa_approvals', ['session_id'], unique=False)
    op.create_index(op.f('ix_rpa_approvals_sku_id'), 'rpa_approvals', ['sku_id'], unique=False)
    op.create_index(op.f('ix_rpa_approvals_task_type'), 'rpa_approvals', ['task_type'], unique=False)
    op.create_index(op.f('ix_rpa_approvals_status'), 'rpa_approvals', ['status'], unique=False)


def downgrade() -> None:
    """只 drop_table：索引与外键随表清掉（MySQL 1553 的教训）。"""
    op.drop_table('rpa_approvals')
