"""fulltext ngram on document_chunks.content

Revision ID: d9e4f1a2b3c5
Revises: c7e2a94b6d18
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e4f1a2b3c5'
down_revision: Union[str, Sequence[str], None] = 'c7e2a94b6d18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """给关键词路加 FULLTEXT ngram 索引（MySQL 侧）。

    背景：关键词路原来是 `LIKE '%词%'` 全表扫（`document_chunk_repository.search_by_keywords`）。
    制度库量级下没问题，块数上到几万后逐行扫会肉眼可见地慢。
    `ngram` parser 是 MySQL 5.7+ 原生中文分词（默认 2 字 token），
    与提词侧"单字全滤、最短 2 字"的口径天然对齐。

    ⚠️ **方言分支是刻意的**：`WITH PARSER ngram` 是 MySQL 专有语法，
    迁移往返测试跑在 SQLite 上（见 tests/test_migrations.py 的口径说明），
    所以非 MySQL 一律跳过 —— 那不是"忘了写"，是 SQLite 没有这个特性，
    测试环境继续走 LIKE 分支（repo 里同一个分支判据）。
    """
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute(
        "CREATE FULLTEXT INDEX ix_document_chunks_content_ft "
        "ON document_chunks (content) WITH PARSER ngram"
    )


def downgrade() -> None:
    """删索引。同 upgrade：非 MySQL 跳过。

    不用 autogenerate：它不认 FULLTEXT 索引，生成的 downgrade 会漏掉它。
    """
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute("DROP INDEX ix_document_chunks_content_ft ON document_chunks")
