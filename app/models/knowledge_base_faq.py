"""知识库 FAQ 表（常见问题置顶）。

**价值在两点**：
  1. 高频重复问题不用每次都调大模型 —— 省 token，也快（走的是本地查表）；
  2. **统一标准答案**：同一个问题让模型答十次，措辞可能十种，
     而制度类问题（报销标准、休假天数）恰恰要求口径一致。

设计取舍：

1. **FAQ 的答案不进向量库、不参与检索。** 它是"命中即返回"的快捷方式，
   与 RAG 是两条独立的路 —— 混进来会让"这个答案来自哪份文档"说不清。

2. **上限 10 条（`FAQ_MAX_PER_KB`）。** FAQ 是置顶的，超过一屏就失去意义了；
   真需要更多应当整理文档，而不是把 FAQ 当第二个知识库。

3. **`sort_order` 小的在前**，允许重复（重复时按 id 兜底，避免顺序抖动）。

⚠️ 权限口径同知识库其他子资源：读 = 库的 read，写 = 库的 admin。
无权与不存在共用 404。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 每个库最多几条 FAQ。超过一屏就失去"置顶"的意义了
FAQ_MAX_PER_KB = 10

QUESTION_MAX_LENGTH = 200
ANSWER_MAX_LENGTH = 2000


class KnowledgeBaseFaq(Base):
    """某知识库的一条常见问题 + 标准答案。"""

    __tablename__ = "knowledge_base_faqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所属知识库（库删了 FAQ 一起走）",
    )
    question: Mapped[str] = mapped_column(
        String(QUESTION_MAX_LENGTH), nullable=False, comment="问题"
    )
    answer: Mapped[str] = mapped_column(
        Text, nullable=False, comment="标准答案（不进向量库、不参与检索）"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
        comment="小的在前；允许重复（重复时按 id 兜底，避免顺序抖动）",
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, comment="创建人（追溯用）"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Faq kb={self.knowledge_base_id} {self.question[:12]!r}>"
