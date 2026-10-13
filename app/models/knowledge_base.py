from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.knowledge_base_faq import KnowledgeBaseFaq
    from app.models.knowledge_base_member import KnowledgeBaseMember
    from app.models.user import User


class KnowledgeBase(Base):
    """知识库（技术 / 人事 / 产品 / 售后 ...）。

    权限不只看这张表，而是 owner_id + is_public + knowledge_base_members 三者组合，
    具体判定逻辑统一放在 services/knowledge_service.py。
    """

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False, comment="知识库名称"
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="创建者"
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
        comment="True 表示所有登录用户都可读，省去逐个授权",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 创建者信息，列表展示要用，joined 避免 N+1
    owner: Mapped["User"] = relationship(lazy="joined")

    # 删除知识库时连带删掉成员记录，不留孤儿行
    members: Mapped[list["KnowledgeBaseMember"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )

    # 文档同理。这里用 ORM 级联而不是只靠数据库外键的 ON DELETE CASCADE：
    # 数据库级联只在真正执行 DELETE 时才生效，而 SQLite（测试库）默认不开启外键约束，
    # 两条路都铺上，行为才在 MySQL 和 SQLite 下一致。
    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )

    # FAQ 同理 —— **这条是补的**：FAQ 表当初只写了数据库侧的 ON DELETE CASCADE，
    # 漏了 ORM 这一路，于是测试库（SQLite 不开外键）里 `delete_kb` 会留下孤儿 FAQ，
    # 而生产库（MySQL）会级联删掉 —— 两边行为不一致，且测试看不见这个差异。
    # 不设 `back_populates`：KnowledgeBaseFaq 那边不需要反向引用，
    # 硬加会逼它也声明一个只为了配对的 relationship。
    faqs: Mapped[list["KnowledgeBaseFaq"]] = relationship(
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"
