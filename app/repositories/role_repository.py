"""角色数据访问。只负责查/写数据库，不做业务判断。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.role import Role


def get_by_code(db: Session, code: str) -> Role | None:
    """按 code 查角色（admin / employee）。"""
    return db.scalar(select(Role).where(Role.code == code))


def list_all(db: Session) -> list[Role]:
    return list(db.scalars(select(Role).order_by(Role.id)))
