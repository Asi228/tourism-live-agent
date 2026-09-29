from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.role import Role


class User(Base):
    """用户表。

    注意：表里只有 password_hash，没有任何地方存明文密码。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, comment="登录名"
    )
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, comment="bcrypt 哈希")
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"), index=True, nullable=False, comment="角色外键"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False, comment="是否启用"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # lazy="joined"：查用户时顺手 JOIN 出角色。
    # 因为每次权限判断都要 user.role.code，用 joined 可以避免 session 关闭后再懒加载报错
    role: Mapped["Role"] = relationship(back_populates="users", lazy="joined")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"
