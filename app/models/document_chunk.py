"""文档切分块表。

正文存在 MySQL，不放 Milvus：
Phase 5 的 Milvus 里只存 chunk_id + kb_id + document_id + 向量，
检索命中后拿 chunk_id 回 MySQL 取正文。这样"向量检索"和"原文展示"都能满足，
也是业界通行做法（Milvus 不适合存大段文本）。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document import Document


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    # 同一份文档里 chunk_index 不能重复：重复处理时先删后插，这条约束能兜住"插了两遍"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_doc_chunk_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="在文档内的序号，从 0 开始"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切分后的正文")
    page_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="起始页码（PDF 从 1 开始；纯文本类文档为 NULL）"
    )
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="本块字符数")
    meta: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="预留扩展：标题、来源行号等"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return (
            f"<DocumentChunk doc={self.document_id} idx={self.chunk_index} "
            f"chars={self.char_count}>"
        )
