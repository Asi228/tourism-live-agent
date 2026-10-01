from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase
    from app.models.user import User

# 权限等级，数字越大权限越高。判断"够不够"时直接比大小。
PERMISSION_LEVEL: dict[str, int] = {"read": 1, "write": 2, "admin": 3}


class KnowledgeBaseMember(Base):
    """知识库成员授权表。

    这张表就是"检索期权限过滤"的数据来源：
    Phase 5 会用它算出允许访问的 knowledge_base_id 列表，再拿去过滤 Milvus。
    """

    __tablename__ = "knowledge_base_members"
    # 同一个人对同一个库只能有一条记录，避免重复授权
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "user_id", name="uq_kb_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    permission: Mapped[str] = mapped_column(
        String(16), default="read", nullable=False, comment="read / write / admin"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeBaseMember kb={self.knowledge_base_id} "
            f"user={self.user_id} perm={self.permission!r}>"
        )
