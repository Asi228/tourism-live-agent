"""add knowledge_base_faqs (pinned FAQ)

Revision ID: c7e2a94b6d18
Revises: b3d5f81a2c74
Create Date: 2026-10-13 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a94b6d18'
down_revision: Union[str, Sequence[str], None] = 'b3d5f81a2c74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    时间默认值用 `CURRENT_TIMESTAMP`（**不是** 前几份迁移里的 `now()`）：
    `now()` 是 MySQL 专有函数，SQLite 会报 `near "(": syntax error`。
    用标准写法这份迁移才能在临时 SQLite 库上实测往返。
    """
    op.create_table(
        'knowledge_base_faqs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('knowledge_base_id', sa.Integer(), nullable=False, comment='所属知识库（库删了 FAQ 一起走）'),
        sa.Column('question', sa.String(length=200), nullable=False, comment='问题'),
        sa.Column('answer', sa.Text(), nullable=False, comment='标准答案（不进向量库、不参与检索）'),
        sa.Column('sort_order', sa.Integer(), nullable=False, comment='小的在前；允许重复（重复时按 id 兜底，避免顺序抖动）'),
        sa.Column('created_by', sa.Integer(), nullable=False, comment='创建人（追溯用）'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_knowledge_base_faqs_knowledge_base_id'),
        'knowledge_base_faqs', ['knowledge_base_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    ⚠️ 同项目其他迁移：**刻意只 drop_table**。MySQL 不允许单独删掉被外键在用的
    索引（错误 1553）—— `knowledge_base_id` 那条正是外键在用的，autogenerate
    生成的"先 drop_index 再 drop_table"会让 downgrade 必失败并把版本号卡住。
    `drop_table` 会连带清掉索引与外键。
    """
    op.drop_table('knowledge_base_faqs')
