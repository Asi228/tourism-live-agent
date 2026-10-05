"""消息的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import select
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
