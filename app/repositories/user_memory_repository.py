"""长期记忆的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)


def list_by_user(db: Session, user_id: int, limit: int | None = None) -> list[UserMemory]:
    """取某用户的记忆，**最近更新的在前**。

    按 `updated_at DESC` 再按 `id DESC`：MySQL 的 DATETIME 只到秒，
    同一秒写入的两条只按时间排会得到不确定的顺序（列表和提示词都会跟着抖）。
    """
    stmt = (
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.updated_at.desc(), UserMemory.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def count_by_user(db: Session, user_id: int) -> int:
    """该用户现有多少条记忆（条数上限的判断用）。"""
    return db.scalar(
        select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id)
    ) or 0


def get_owned(db: Session, memory_id: int, user_id: int) -> UserMemory | None:
    """按 id + 归属取一条；不归当前用户就返回 None。

    ⚠️ 归属直接写进 WHERE，而不是"先按 id 查出来再比 user_id"——
    后者是典型的越权写法：只要有人在中间忘了那次比较，别人的记忆就漏出去了。
    """
    return db.scalar(
        select(UserMemory).where(
            UserMemory.id == memory_id, UserMemory.user_id == user_id
        )
    )


def create(
    db: Session,
    user_id: int,
    content: str,
    source_conversation_id: int | None = None,
) -> UserMemory:
    row = UserMemory(
        user_id=user_id, content=content, source_conversation_id=source_conversation_id
    )
    db.add(row)
    db.flush()
    return row


def delete(db: Session, memory: UserMemory) -> None:
    """删一条记忆。**不可逆**，所以调用方（service）必须先做完归属校验。

    这里用 ORM 的 `db.delete(row)` 而不是 `delete(UserMemory).where(...)`：
    后者需要 `from sqlalchemy import delete`，而本模块的删除函数**也叫 delete** ——
    函数定义会把那个名字重新绑定成自己，于是 `delete(UserMemory)` 变成"把模型当 db 传"，
    报 `missing 1 required positional argument`。用 ORM 接口从根上避开这个遮蔽。
    """
    db.delete(memory)
    db.flush()
