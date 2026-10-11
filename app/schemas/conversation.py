"""会话相关的响应模型（Phase 9）。

只有读模型：会话的创建是**隐式**的（首次提问时后端自动建），删是唯一写操作且不需要请求体。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import SourceItem


class MessageOut(BaseModel):
    """一条消息。

    `sources` / `meta` 只在 assistant 消息上有值 —— 它们是**回显**用的
    （前端重放历史时要能画出当时的引用卡片和标签），不参与检索或统计。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    content: str
    sources: list[SourceItem] | None = None
    meta: dict | None = Field(default=None, description="answer_mode / usage / elapsed_ms 等展示信息")
    created_at: datetime


class ConversationItem(BaseModel):
    """会话列表项。

    刻意**不带消息正文** —— 列表页只要标题和时间，把每段对话都拉出来纯属浪费带宽。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message_count: int = Field(default=0, description="会话里有多少条消息（一问一答算 2 条）")
    created_at: datetime
    updated_at: datetime = Field(description="最近一次发言时间 —— 列表按它倒序")


class ConversationDetail(BaseModel):
    """会话详情：一页消息 + "还有没有更早的"，用于点开历史会话时还原界面。

    `has_more=True` 表示这条会话在返回的这批消息之前还有内容，
    前端可以据此显示"加载更早"，再用 `before_id=消息里最小的 id` 往前翻。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    has_more: bool = Field(
        default=False, description="返回的这一页之前是否还有更早的消息"
    )
    messages: list[MessageOut] = Field(default_factory=list)
