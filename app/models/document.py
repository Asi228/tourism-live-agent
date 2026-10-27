"""文档表。"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_chunk import DocumentChunk
    from app.models.knowledge_base import KnowledgeBase
    from app.models.live_commerce import Product, ProductSku
    from app.models.user import User

# 文档处理状态。用字符串而不是枚举表：
# 状态集合是代码里固定的四种流转，没有"让管理员自己加状态"的需求，
# 建一张状态字典表只增加一次 JOIN。
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ALL_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_FAILED)

DOC_TYPE_GENERAL = "general"
DOC_TYPE_PRODUCT_MANUAL = "product_manual"
DOC_TYPE_CLAIM_EVIDENCE = "claim_evidence"
DOC_TYPE_PLATFORM_RULE = "platform_rule"
DOC_TYPE_AFTER_SALES = "after_sales"
DOC_TYPE_LOGISTICS = "logistics"
DOC_TYPE_LIVE_POLICY = "live_policy"
DOC_TYPE_REPLAY_REPORT = "replay_report"
DOCUMENT_TYPES = (
    DOC_TYPE_GENERAL,
    DOC_TYPE_PRODUCT_MANUAL,
    DOC_TYPE_CLAIM_EVIDENCE,
    DOC_TYPE_PLATFORM_RULE,
    DOC_TYPE_AFTER_SALES,
    DOC_TYPE_LOGISTICS,
    DOC_TYPE_LIVE_POLICY,
    DOC_TYPE_REPLAY_REPORT,
)
PRODUCT_SCOPED_DOCUMENT_TYPES = (DOC_TYPE_PRODUCT_MANUAL, DOC_TYPE_CLAIM_EVIDENCE)


class Document(Base):
    """一份上传到知识库的文件。

    为什么 filename 和 stored_name 要分两个字段：
      filename    —— 用户看到的原始文件名，可能重名、可能含中文空格、可能含 ../ 
      stored_name —— 磁盘上的实际文件名（uuid + 扩展名），唯一且安全
    两者分开之后，磁盘路径永远由服务端生成，客户端传什么都不影响落盘位置。
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    uploader_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="上传者"
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="关联商品；平台规则等通用资料可为空",
    )
    sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_skus.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="关联到具体 SKU；为空表示适用于整个商品",
    )
    document_type: Mapped[str] = mapped_column(
        String(32), default=DOC_TYPE_GENERAL, server_default=DOC_TYPE_GENERAL,
        index=True, nullable=False, comment="业务资料类型",
    )
    version: Mapped[str] = mapped_column(
        String(32), default="v1", server_default="v1", nullable=False,
        comment="业务资料版本",
    )
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime, index=True, nullable=True, comment="生效时间；为空表示不限制起始时间"
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime, index=True, nullable=True, comment="失效时间；为空表示长期有效"
    )

    filename: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="原始文件名（展示用）"
    )
    stored_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="磁盘文件名（uuid，服务端生成）"
    )
    file_path: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="绝对路径，仅服务端使用，不对客户端暴露"
    )
    file_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="扩展名，小写不带点：pdf / txt / md"
    )
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="字节数")

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PENDING, server_default=STATUS_PENDING,
        index=True, nullable=False, comment="pending / processing / completed / failed",
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    char_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="清洗后总字符数"
    )
    error_msg: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="处理失败原因（给用户看的，不含堆栈）"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(
        back_populates="documents", lazy="joined"
    )
    uploader: Mapped["User"] = relationship(lazy="joined")
    product: Mapped["Product | None"] = relationship(foreign_keys=[product_id], lazy="joined")
    sku: Mapped["ProductSku | None"] = relationship(foreign_keys=[sku_id], lazy="joined")

    # 删除文档时连带删掉它的 chunk，不留孤儿行
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r} status={self.status!r}>"
