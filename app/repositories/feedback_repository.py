"""答案反馈的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message_feedback import MessageFeedback

logger = logging.getLogger(__name__)


def get_by_message_and_user(
    db: Session, message_id: int, user_id: int
) -> MessageFeedback | None:
    """某人对某条消息的现有评价（没有则 None）。

    ⚠️ 两个条件都写进 WHERE：只按 message_id 查会拿到**别人**的评价，
    反馈内容是私密数据，不能这样漏。
    """
    return db.scalar(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        )
    )


def upsert(
    db: Session,
    message_id: int,
    user_id: int,
    rating: str,
    reason: str | None,
    comment: str | None,
) -> MessageFeedback:
    """写入或更新一次评价。

    **刻意是 upsert 而不是 insert**：唯一约束 `(message_id, user_id)` 意味着
    同一个人对同一条消息只能有一条评价；用户改主意（先点了没用、后又觉得有用）
    是常态，所以重复提交要走更新而不是报"你已经评价过了"。
    """
    row = get_by_message_and_user(db, message_id, user_id)
    if row is None:
        row = MessageFeedback(
            message_id=message_id,
            user_id=user_id,
            rating=rating,
            reason=reason,
            comment=comment,
        )
        db.add(row)
    else:
        row.rating = rating
        row.reason = reason
        row.comment = comment
    db.flush()
    return row
