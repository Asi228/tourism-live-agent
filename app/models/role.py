from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    # 只在类型检查时导入，运行时不导入 —— 用来避免 Role <-> User 循环导入
    from app.models.user import User


class Role(Base):
    """角色表。第一版只有两个角色：admin（管理员）、employee（普通员工）。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # code 是程序里判断权限用的稳定标识；name 只用于展示，可以随便改
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="角色标识")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名称")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # 反向关系：一个角色对应多个用户
    users: Mapped[list["User"]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role id={self.id} code={self.code!r}>"
