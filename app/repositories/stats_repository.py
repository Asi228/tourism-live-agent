"""概览统计的聚合查询。

为什么不塞进 message / document 各自的 repository：这些查询**天生跨表**
（文档数要按"我可见的库"过滤、问答数要和会话关联）。硬塞进去会让那两个
repository 的职责从"管某张表"变成"管某张表 + 某些统计"，边界就糊了。

全部是**只读聚合**，不产生业务对象，所以这里直接返回元组/dict 而不是 ORM 实体。
"""

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.document import Document
from app.models.message import ROLE_USER, Message
from app.models.user import User


def document_counts(db: Session, kb_ids: list[int] | None) -> tuple[int, dict[str, int]]:
    """返回 (文档总数, {状态: 条数})。

    `kb_ids=None` 表示不受限（管理员）。空列表是"没有任何可见库"，直接返回 0 ——
    **不能把 [] 当成 None 处理**，那会让无权用户看到全量统计。
    """
    if kb_ids == []:
        return 0, {}
    stmt = select(Document.status, func.count()).group_by(Document.status)
    if kb_ids is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(kb_ids))
    rows = db.execute(stmt).all()
    by_status = {status: count for status, count in rows}
    return sum(by_status.values()), by_status


def question_counts(db: Session, user_id: int) -> tuple[int, int]:
    """返回 (累计提问数, 今日提问数)。

    口径用 `messages` 里 `role=user` 的条数 —— 两条问答路（RAG / Agent）都会写
    messages，而 `agent_logs` **只有 Agent 那条路写**，拿它统计会漏掉一半。

    今日用**时间范围**而不是 `DATE(created_at) = 今天`：后者要把函数套在列上，
    既用不上索引，MySQL 与 SQLite 的日期函数写法还不一样（跨库会翻车）。
    """
    total = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.role == ROLE_USER, Conversation.user_id == user_id)
        )
        or 0
    )
    today_start = datetime.combine(date.today(), time.min)
    today = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Message.role == ROLE_USER,
                Message.created_at >= today_start,
                Conversation.user_id == user_id,
            )
        )
        or 0
    )
    return total, today


def recent_documents(db: Session, kb_ids: list[int] | None, limit: int = 5) -> list[dict]:
    """最近上传的文档。输出键与 `DocumentOut` 对齐（`knowledge_base_id`）。"""
    if kb_ids == []:
        return []
    stmt = select(Document).order_by(Document.id.desc()).limit(limit)
    if kb_ids is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(kb_ids))
    return [
        {
            "id": d.id, "filename": d.filename, "status": d.status,
            "knowledge_base_id": d.knowledge_base_id,
            "chunk_count": d.chunk_count, "created_at": d.created_at,
        }
        for d in db.execute(stmt).scalars().all()
    ]


def recent_questions(db: Session, user_id: int, limit: int = 5) -> list[dict]:
    """最近问过的问题（带会话标题，便于点回去）。"""
    rows = (
        db.execute(
            select(Message, Conversation.title)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.role == ROLE_USER, Conversation.user_id == user_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        .all()
    )
    return [
        {
            "conversation_id": m.conversation_id,
            "conversation_title": title,
            "query": m.content[:120],
            "created_at": m.created_at,
        }
        for m, title in rows
    ]


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0
