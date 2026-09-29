"""用户数据访问。只负责查/写数据库，不做业务判断（比如"用户名是否重复"由 Service 决定）。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(db: Session, user_id: int) -> User | None:
    # db.get 走主键，会先用 identity map，是最快的按 id 查法
    return db.get(User, user_id)


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def create(
    db: Session,
    *,
    username: str,
    email: str,
    password_hash: str,
    role_id: int,
) -> User:
    """插入用户并返回带 id 的实例。"""
    user = User(
        username=username,
        email=email,
        password_hash=password_hash,
        role_id=role_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)  # 取回数据库生成的 id / created_at
    return user
