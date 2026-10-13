"""消息的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.message import Message

logger = logging.getLogger(__name__)


def get_by_id(db: Session, message_id: int) -> Message | None:
    """按主键取一条消息。

    ⚠️ **不做归属判断** —— 归属是会话的属性（消息挂在会话下），
    该由调用方走 `conversation_service.get_owned` 判定。
    这里只负责"有没有这条记录"，避免数据访问层自己发明一套权限语义。
    """
    return db.get(Message, message_id)


def list_by_conversation(db: Session, conversation_id: int) -> list[Message]:
    """按时间正序取一个会话的全部消息。"""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(db.scalars(stmt))


def page_by_conversation(
    db: Session, conversation_id: int, limit: int, before_id: int | None = None
) -> tuple[list[Message], bool]:
    """取会话里**最新**的 limit 条（`before_id` 之前的那一段），按时间正序返回。

    返回 `(消息列表, 是否还有更早的)`。

    **为什么是 keyset（`id < before_id`）而不是 offset**：会话的头部在不断长出新消息，
    offset 分页在"翻页期间又来了新消息"时会漏条/重条 —— 与列表页那个 offset 场景正好相反
    （那边翻的是不再变化的存量，这边翻的是活跃增长的尾部）。`id` 单调递增，
    拿"当前最旧那条的 id"当下次游标，翻页期间来多少新消息都不影响。

    **多查一条来判断 has_more**：比再发一次 COUNT 便宜，而且天然一致
    —— 两次查询之间又追加了消息的话，COUNT 的答案就已经过期了。
    """
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)

    rows = list(db.scalars(stmt.order_by(Message.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    return list(reversed(rows[:limit])), has_more


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


def list_between(
    db: Session, conversation_id: int, after_id: int, before_id: int
) -> list[Message]:
    """取 id **落在 `(after_id, before_id)` 之间**的消息，按时间正序。

    给"长对话摘要"用（见 `conversation_service.maybe_refresh_summary`）：
    它要的正是"已经滑出最近 N 轮窗口、但还没被摘要覆盖"的那一段。
    两端都用 `id` 比较而不是时间：`id` 单调递增、且同一秒内也不含糊。
    """
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.id > after_id,
            Message.id < before_id,
        )
        .order_by(Message.id)
    )
    return list(db.scalars(stmt))


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
