"""答案反馈接口（Bad Case 闭环）。

  POST  /api/messages/{id}/feedback    给一条回答打分（重复提交 = 更新）
  GET   /api/messages/{id}/feedback    读自己在这条上的评价（前端回显用）

**为什么放在 `/messages` 而不是 `/conversations/{id}/messages/{id}`**：
反馈只关心"哪条消息"，会话 id 是冗余的一层；而归属校验在 service 内部
由消息反查会话完成，不需要调用方先知道会话号。

归属校验一律 404（口径与会话、记忆一致）。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.feedback import FeedbackIn, FeedbackOut
from app.services import feedback_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["答案反馈"])


@router.post(
    "/{message_id}/feedback",
    response_model=ApiResponse[FeedbackOut],
    summary="给一条回答打分（可改）",
)
def rate_message(
    message_id: int,
    payload: FeedbackIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """「有用 / 没用」。**重复提交是更新而不是报错** —— 用户改主意是常态，
    报"你已经评价过了"只会逼他把错的留在那儿。

    点「没用」时可以带一个原因（答案错误 / 与问题无关 / 文不对题 /
    信息过时 / 其他）和备注。好评带的原因会被丢弃（那是矛盾数据）。
    """
    row = feedback_service.upsert_feedback(
        db, current_user, message_id,
        rating=payload.rating,
        reason=payload.reason,
        comment=payload.comment,
    )
    return success(FeedbackOut.model_validate(row), "感谢反馈")


@router.get(
    "/{message_id}/feedback",
    response_model=ApiResponse[FeedbackOut | None],
    summary="读自己在这条回答上的评价",
)
def read_my_feedback(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """没有评价时 `data` 是 null（不是报错）—— 前端据此决定按钮要不要高亮。"""
    row = feedback_service.get_my_feedback(db, current_user, message_id)
    return success(FeedbackOut.model_validate(row) if row else None)
