"""add message_feedbacks (answer feedback / bad-case loop)

Revision ID: b3d5f81a2c74
Revises: a1c4e7f29b30
Create Date: 2026-10-13 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d5f81a2c74'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7f29b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    ⚠️ 这里的时间默认值用 `CURRENT_TIMESTAMP` 而不是兄弟迁移里的 `now()`：
    `now()` 是 **MySQL 专有函数**，SQLite 会直接报
    `near "(": syntax error` —— 也就是说那几份迁移在 SQLite 上跑不起来
    （本项目测试库正是 SQLite，所以它们从来没被测过）。
    `CURRENT_TIMESTAMP` 是标准 SQL，MySQL 与 SQLite 都认、语义等价，
    换掉它唯一的好处是**这份迁移可以在临时 SQLite 库上实测往返**。
    """
    op.create_table(
        'message_feedbacks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False, comment='被评价的消息（消息被裁剪删除时，反馈一起走）'),
        sa.Column('user_id', sa.Integer(), nullable=False, comment='评价人'),
        sa.Column('rating', sa.String(length=8), nullable=False, comment='up / down（见模块常量 RATING_*）'),
        sa.Column('reason', sa.String(length=32), nullable=True, comment='差评原因（见模块常量 REASON_*）；好评为 null'),
        sa.Column('comment', sa.Text(), nullable=True, comment='补充备注，最多 500 字'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'user_id', name='uq_feedback_message_user'),
    )
    op.create_index(op.f('ix_message_feedbacks_message_id'), 'message_feedbacks', ['message_id'], unique=False)
    op.create_index(op.f('ix_message_feedbacks_user_id'), 'message_feedbacks', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    ⚠️ 同项目其他迁移：**刻意只 drop_table**，不用 autogenerate 生成的
    "先 drop_index 再 drop_table"。MySQL 不允许单独删掉被外键在用的索引
    （错误 1553）—— `message_id` 那条索引正是外键在用的，那样写会让
    downgrade 必失败并把版本号卡在中间。`drop_table` 会连带清掉索引与外键。
    """
    op.drop_table('message_feedbacks')
