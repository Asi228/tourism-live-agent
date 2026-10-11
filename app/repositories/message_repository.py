"""消息的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.message import Message

logger = logging.getLogger(__name__)


def list_by_conversation(db: Session, conversation_id: int) -> list[Message]:
    """按时间正序取一个会话的全部消息（详情页/加载历史用）。"""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(db.scalars(stmt))


def recent(db: Session, conversation_id: int, limit: int) -> list[Message]:
    """取**最近** limit 条，但仍按时间正序返回。

    两段式的原因：`ORDER BY id DESC LIMIT n` 才是"最近 n 条"，
    但喂给模型时必须按时间正序（先问先答），所以要再倒回来。
    先按 ASC 取会拿到最早的那几条 —— 那是完全不同的语义。
    """
    if limit <= 0:
        return []
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(db.scalars(stmt))))


def count_by_conversation(db: Session, conversation_id: int) -> int:
    """一个会话里现有多少条消息（数据保留的口径判断用）。"""
    return db.scalar(
        select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation_id
        )
    ) or 0


def delete_oldest(db: Session, conversation_id: int, count: int) -> int:
    """删掉该会话里**最旧**的 count 条消息，返回实际删除条数。

    只服务数据保留（见 `conversation_service._trim_messages`），日常没有任何调用方。
    ⚠️ **不可逆**：调用方负责把 count 凑成**整轮**（一问一答 = 2 条），
    这里不替它猜 —— "删成半条轮次"这种判断属于业务口径，不该藏在数据访问层。
    """
    if count <= 0:
        return 0
    ids = list(
        db.scalars(
            select(Message.id)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
            .limit(count)
        )
    )
    if not ids:
        return 0
    db.execute(delete(Message).where(Message.id.in_(ids)))
    db.flush()
    return len(ids)


def create(
    db: Session,
    conversation_id: int,
    role: str,
    content: str,
    sources: list | None = None,
    meta: dict | None = None,
) -> Message:
    row = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=sources,
        meta=meta,
    )
    db.add(row)
    db.flush()
    return row
