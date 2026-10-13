"""add user_memories.is_enabled (per-memory on/off switch)

Revision ID: a1c4e7f29b30
Revises: 6f5b38de538b
Create Date: 2026-10-13 17:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e7f29b30'
down_revision: Union[str, Sequence[str], None] = '6f5b38de538b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default="1" 是给**存量行**兜的：加列时 MySQL/SQLite 都会把已有行填成 1，
    # 否则老记忆会一夜之间全部"关掉"、拼不进提示词（用户毫无感知地丢了功能）。
    # `default=True`（模型侧）管的是以后新建的对象，两边都要写，别只写一个。
    op.add_column(
        'user_memories',
        sa.Column(
            'is_enabled',
            sa.Boolean(),
            nullable=False,
            server_default='1',
            comment='False 表示暂时不拼进提示词（数据仍在，可随时开回来）',
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    ⚠️ SQLite 上 `drop_column` 需要 batch 模式（SQLAlchemy 用"建新表→搬数据→换表"实现），
    而本项目测试库正是 SQLite —— 不套 batch_alter_table 的话 `downgrade` 会直接报
    "Cannot drop column in SQLite"。生产库是 MySQL，那条路上 drop_column 是原生支持的，
    套了 batch 也不影响（MySQL 下 batch 退化为普通 ALTER）。
    """
    with op.batch_alter_table('user_memories') as batch:
        batch.drop_column('is_enabled')
