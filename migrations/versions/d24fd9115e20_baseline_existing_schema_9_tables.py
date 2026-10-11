"""baseline: existing schema (9 tables)

Revision ID: d24fd9115e20
Revises:
Create Date: 2026-10-11 16:00:30.536150

⚠️ 这份基线**最初是空的**（只 stamp 版本、不建表），因为那 9 张表先于 Alembic 存在。
但空基线有个致命后果：**在一个真正的空库上 `alembic upgrade head` 会失败** ——
下一份迁移要建 `user_memories`，它的外键指向 `users.id`，而 `users` 压根不存在
（实测 `(1824, "Failed to open the referenced table 'users'")`）。
也就是说"clone 下来先跑 `alembic upgrade head`"这条路走不通，Alembic 只剩"给老库打补丁"。

所以这里补上这 9 张表的建表语句，让**空库也能靠迁移从零建起**：

  · 老库：版本号早就 stamp 在 d24fd9115e20 上，这份 migration 不会再跑一次（真正的幂等）；
  · 空库：upgrade 一路建到底。

⚠️ `downgrade()` 刻意**只用 `op.drop_table`**，不用 autogenerate 那套"先 drop_index 再 drop_table"：
MySQL 不允许单独删掉外键在用的索引（`1553, needed in a foreign key constraint`），
而删表本身会连带删掉它的索引与外键。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd24fd9115e20'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把 9 张表一次建出来（顺序就是外键依赖顺序，别乱动）。"""
    op.create_table('roles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='角色标识'),
    sa.Column('name', sa.String(length=64), nullable=False, comment='角色名称'),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False, comment='登录名'),
    sa.Column('email', sa.String(length=128), nullable=False),
    sa.Column('password_hash', sa.String(length=128), nullable=False, comment='bcrypt 哈希'),
    sa.Column('role_id', sa.Integer(), nullable=False, comment='角色外键'),
    sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False, comment='是否启用'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_role_id'), 'users', ['role_id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('agent_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='发起人'),
    sa.Column('query', sa.String(length=1000), nullable=False, comment='用户问题'),
    sa.Column('answer', sa.Text(), nullable=True, comment='最终答案（便于复盘）'),
    sa.Column('hit', sa.Boolean(), server_default='0', nullable=False, comment='是否检索到了资料'),
    sa.Column('source_count', sa.Integer(), server_default='0', nullable=False, comment='引用了多少条资料'),
    sa.Column('iterations', sa.Integer(), server_default='0', nullable=False, comment='实际检索轮数'),
    sa.Column('steps', sa.JSON(), nullable=True, comment='每步轨迹，如 [{"node":"retrieve","ms":820,"iteration":1}]'),
    sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False, comment='本次消耗的 token 合计'),
    sa.Column('elapsed_ms', sa.Integer(), server_default='0', nullable=False, comment='总耗时（毫秒）'),
    sa.Column('error_msg', sa.String(length=500), nullable=True, comment='失败原因（不含堆栈）'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_logs_created_at'), 'agent_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_agent_logs_user_id'), 'agent_logs', ['user_id'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='会话归属，只有本人能看'),
    sa.Column('title', sa.String(length=60), nullable=False, comment='取自首个问题，便于在列表里认出来'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_updated_at'), 'conversations', ['updated_at'], unique=False)
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_table('knowledge_bases',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False, comment='知识库名称'),
    sa.Column('description', sa.String(length=512), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=False, comment='创建者'),
    sa.Column('is_public', sa.Boolean(), server_default='0', nullable=False, comment='True 表示所有登录用户都可读，省去逐个授权'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_bases_name'), 'knowledge_bases', ['name'], unique=True)
    op.create_index(op.f('ix_knowledge_bases_owner_id'), 'knowledge_bases', ['owner_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('knowledge_base_id', sa.Integer(), nullable=False),
    sa.Column('uploader_id', sa.Integer(), nullable=False, comment='上传者'),
    sa.Column('filename', sa.String(length=255), nullable=False, comment='原始文件名（展示用）'),
    sa.Column('stored_name', sa.String(length=255), nullable=False, comment='磁盘文件名（uuid，服务端生成）'),
    sa.Column('file_path', sa.String(length=512), nullable=False, comment='绝对路径，仅服务端使用，不对客户端暴露'),
    sa.Column('file_type', sa.String(length=16), nullable=False, comment='扩展名，小写不带点：pdf / txt / md'),
    sa.Column('file_size', sa.BigInteger(), nullable=False, comment='字节数'),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False, comment='pending / processing / completed / failed'),
    sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('char_count', sa.Integer(), server_default='0', nullable=False, comment='清洗后总字符数'),
    sa.Column('error_msg', sa.Text(), nullable=True, comment='处理失败原因（给用户看的，不含堆栈）'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_documents_knowledge_base_id'), 'documents', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)
    op.create_index(op.f('ix_documents_uploader_id'), 'documents', ['uploader_id'], unique=False)
    op.create_table('knowledge_base_members',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('knowledge_base_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('permission', sa.String(length=16), nullable=False, comment='read / write / admin'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('knowledge_base_id', 'user_id', name='uq_kb_member')
    )
    op.create_index(op.f('ix_knowledge_base_members_knowledge_base_id'), 'knowledge_base_members', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_knowledge_base_members_user_id'), 'knowledge_base_members', ['user_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False, comment='user 或 assistant（见模块常量 ROLE_*）'),
    sa.Column('content', sa.Text(), nullable=False, comment='问题原文 / 答案正文'),
    sa.Column('sources', sa.JSON(), nullable=True, comment='引用列表，与答案里的 [n] 编号一一对应'),
    sa.Column('meta', sa.JSON(), nullable=True, comment='展示用杂项：answer_mode / hit / reranked / usage / elapsed_ms / model / searched_kb_ids'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_messages_created_at'), 'messages', ['created_at'], unique=False)
    op.create_table('document_chunks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False, comment='在文档内的序号，从 0 开始'),
    sa.Column('content', sa.Text(), nullable=False, comment='切分后的正文'),
    sa.Column('page_number', sa.Integer(), nullable=True, comment='起始页码（PDF 从 1 开始；纯文本类文档为 NULL）'),
    sa.Column('char_count', sa.Integer(), nullable=False, comment='本块字符数'),
    sa.Column('meta', sa.JSON(), nullable=True, comment='预留扩展：标题、来源行号等'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'chunk_index', name='uq_doc_chunk_index')
    )
    op.create_index(op.f('ix_document_chunks_document_id'), 'document_chunks', ['document_id'], unique=False)


def downgrade() -> None:
    """按依赖倒序删表。⚠️ 只 `drop_table`（见文件头：MySQL 不允许单独删外键在用的索引）。"""
    op.drop_table('document_chunks')
    op.drop_table('messages')
    op.drop_table('knowledge_base_members')
    op.drop_table('documents')
    op.drop_table('knowledge_bases')
    op.drop_table('conversations')
    op.drop_table('agent_logs')
    op.drop_table('users')
    op.drop_table('roles')
