"""add rpa_tasks / rpa_task_steps / mock_platform_skus

Revision ID: e9c2b7d14a58
Revises: d7e1a5c83f29
Create Date: 2026-10-21 22:20:00.000000

⚠️ 为什么补这份迁移：`app/models/rpa.py` 的三张表**模型写了、迁移漏了** ——
真库直接缺表，`/api/rpa/*` 与模拟平台页面一访问就 1146（table doesn't exist）。
按项目口径"schema 唯一权威是迁移"，模型存在不算数，必须在迁移里建出来。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9c2b7d14a58'
down_revision: Union[str, Sequence[str], None] = 'd7e1a5c83f29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rpa_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_rpa_tasks_idempotency_key'),
    )
    op.create_index(op.f('ix_rpa_tasks_session_id'), 'rpa_tasks', ['session_id'], unique=False)
    op.create_index(op.f('ix_rpa_tasks_task_type'), 'rpa_tasks', ['task_type'], unique=False)
    op.create_index(op.f('ix_rpa_tasks_status'), 'rpa_tasks', ['status'], unique=False)

    op.create_table(
        'rpa_task_steps',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('step_code', sa.String(length=64), nullable=False),
        sa.Column('step_name', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('message', sa.String(length=512), nullable=True),
        sa.Column('screenshot_path', sa.String(length=512), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['rpa_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rpa_task_steps_task_id'), 'rpa_task_steps', ['task_id'], unique=False)

    op.create_table(
        'mock_platform_skus',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sku_code', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('platform_status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('list_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('live_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('available_quantity', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reserved_quantity', sa.Integer(), server_default='0', nullable=False),
        sa.Column('promotion_starts_at', sa.DateTime(), nullable=True),
        sa.Column('promotion_ends_at', sa.DateTime(), nullable=True),
        sa.Column('stackable', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # 模型是 unique=True + index=True → SQLAlchemy 生成的是 **unique index**（不是唯一约束），
    # 这里用同一种形态，drift 检查才认为一致。
    op.create_index(
        op.f('ix_mock_platform_skus_sku_code'), 'mock_platform_skus', ['sku_code'], unique=True
    )

    # ---- 顺手对齐 documents 的 4 个陈旧列注释 ----
    # 模型上的注释在引入商品/资料关联时改过，但那批改动没生成迁移，
    # 于是 drift 检查一直报 modify_comment。comment 只改元数据、不动数据，安全。
    op.alter_column('documents', 'product_id',
                    existing_type=sa.Integer(), existing_nullable=True,
                    comment='关联商品；平台规则等通用资料可为空')
    op.alter_column('documents', 'sku_id',
                    existing_type=sa.Integer(), existing_nullable=True,
                    comment='关联到具体 SKU；为空表示适用于整个商品')
    op.alter_column('documents', 'effective_from',
                    existing_type=sa.DateTime(), existing_nullable=True,
                    comment='生效时间；为空表示不限制起始时间')
    op.alter_column('documents', 'effective_to',
                    existing_type=sa.DateTime(), existing_nullable=True,
                    comment='失效时间；为空表示长期有效')


def downgrade() -> None:
    """只 drop_table（同项目其它迁移）：索引与外键随表一起清掉，
    避免 MySQL 1553（外键在用的索引不能单独删）。"""
    op.drop_table('mock_platform_skus')
    op.drop_table('rpa_task_steps')
    op.drop_table('rpa_tasks')
