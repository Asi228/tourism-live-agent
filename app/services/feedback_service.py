"""答案反馈的编排（Bad Case 闭环）。

两件事：写入/更新一次评价、读取自己那条。事务边界在 service 层（项目硬约定）。

⚠️ 归属校验一律 404（与会话、记忆一致）：403 等于确认"这个 id 存在，
只是不归你"，白送一条可枚举的信息。路径是
「消息 → 会话 → `conversation_service.get_owned`」，不自己拼 SQL。
"""

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models import ROLE_ASSISTANT
from app.models.message import Message
from app.models.message_feedback import (
    ALL_RATINGS,
    ALL_REASONS,
    COMMENT_MAX_LENGTH,
    RATING_DOWN,
    MessageFeedback,
)
from app.models.user import User
from app.repositories import feedback_repository, message_repository
from app.services import conversation_service

logger = logging.getLogger(__name__)


def _load_rateable_message(db: Session, user: User, message_id: int) -> Message:
    """取出"可以被当前用户评价"的那条消息，否则抛异常。

    三道检查，缺一不可：
      1. 消息存在吗            → 不存在 404
      2. 消息所在会话归他吗    → 不归 404（别人的消息连存在性都不该确认）
      3. 是 assistant 消息吗   → 不是 40000（给自己的提问打"答案错误"没意义）
    """
    row = message_repository.get_by_id(db, message_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "消息不存在")

    # 归属靠会话判定：get_owned 把 user_id 写进 WHERE
    conversation_service.get_owned(db, user, row.conversation_id)

    if row.role != ROLE_ASSISTANT:
        raise AppException(ErrorCode.PARAM_ERROR, "只能评价助手的回答")
    return row


def upsert_feedback(
    db: Session,
    user: User,
    message_id: int,
    rating: str,
    reason: str | None = None,
    comment: str | None = None,
) -> MessageFeedback:
    """对一条回答打分。**重复提交是更新，不是报错**（用户改主意是常态）。"""
    if rating not in ALL_RATINGS:
        raise AppException(ErrorCode.PARAM_ERROR, "评分只能是 up 或 down")

    _load_rateable_message(db, user, message_id)

    text = (comment or "").strip() or None
    if text and len(text) > COMMENT_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"备注最多 {COMMENT_MAX_LENGTH} 字"
        )

    # 好评不带原因：给"有用"配一个"答案错误"是矛盾数据，直接丢弃比存着好
    if rating != RATING_DOWN:
        reason = None
    elif reason is not None and reason not in ALL_REASONS:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"差评原因只能是：{'、'.join(ALL_REASONS)}"
        )

    row = feedback_repository.upsert(
        db, message_id, user.id, rating, reason, text
    )
    db.commit()
    logger.info(
        "答案反馈 message_id=%s user=%s rating=%s reason=%s",
        message_id, user.username, rating, reason,
    )
    return row


def get_my_feedback(
    db: Session, user: User, message_id: int
) -> MessageFeedback | None:
    """读自己在这条消息上的评价（前端据此回显按钮状态）。没有则 None。"""
    row = message_repository.get_by_id(db, message_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "消息不存在")
    conversation_service.get_owned(db, user, row.conversation_id)
    return feedback_repository.get_by_message_and_user(db, message_id, user.id)
