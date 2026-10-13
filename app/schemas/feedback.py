"""答案反馈的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.message_feedback import (
    ALL_RATINGS,
    ALL_REASONS,
    COMMENT_MAX_LENGTH,
)


class FeedbackIn(BaseModel):
    """一次评价。

    `reason` 只在差评时有意义 —— 好评带原因是矛盾数据，service 层会直接丢掉，
    所以这里不要求前端自己清空。
    """

    rating: str = Field(description=f"up / down（{'、'.join(ALL_RATINGS)}）")
    reason: str | None = Field(
        default=None, description=f"差评原因，取：{'、'.join(ALL_REASONS)}"
    )
    comment: str | None = Field(
        default=None, max_length=COMMENT_MAX_LENGTH, description="补充备注"
    )


class FeedbackOut(BaseModel):
    """一条评价的对外形态。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    rating: str
    reason: str | None = None
    comment: str | None = None
    created_at: datetime
    updated_at: datetime
