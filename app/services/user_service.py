"""用户查询业务逻辑（注册/登录在 auth_service，这里只有查）。"""

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories import user_repository


def list_users(db: Session, keyword: str | None = None, limit: int = 50) -> list[User]:
    """按关键字定位用户。返回 ORM 对象，对外暴露哪些字段由接口的 Schema 决定。"""
    return user_repository.search(db, keyword, limit)
