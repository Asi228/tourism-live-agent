"""用户数据访问。只负责查/写数据库，不做业务判断（比如"用户名是否重复"由 Service 决定）。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(db: Session, user_id: int) -> User | None:
    # db.get 走主键，会先用 identity map，是最快的按 id 查法
    return db.get(User, user_id)


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def search(db: Session, keyword: str | None, limit: int) -> list[User]:
    """按用户名 / 邮箱模糊查，给管理员定位 user_id 用（授权知识库成员需要 id）。

    User.role 是 lazy="joined"，所以角色信息在同一条 SELECT 里就带出来了。
    """
    stmt = select(User).order_by(User.id).limit(limit)
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(or_(User.username.ilike(pattern), User.email.ilike(pattern)))
    return list(db.scalars(stmt))


def create(
    db: Session,
    *,
    username: str,
    email: str,
    password_hash: str,
    role_id: int,
) -> User:
    """插入用户并返回带 id 的实例。

    只 flush 不 commit：事务边界归 Service 层，Repository 无权结束事务。
    flush 会把 INSERT 发给数据库并取回自增 id，但事务还没提交，
    Service 里后续任何一步失败都能把这条记录一起回滚掉。
    """
    user = User(
        username=username,
        email=email,
        password_hash=password_hash,
        role_id=role_id,
    )
    db.add(user)
    db.flush()
    return user
