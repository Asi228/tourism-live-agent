from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
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

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"
