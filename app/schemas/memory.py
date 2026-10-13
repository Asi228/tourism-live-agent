"""长期记忆的请求 / 响应模型（Phase 11）。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user_memory import CONTENT_MAX_LENGTH


class MemoryCreate(BaseModel):
    """写入一条记忆。

    只支持**显式**写入：由用户（或前端界面）明确说"记住这件事"。
    刻意不做"每轮问答自动提炼"——那要多花一次模型调用，而且会把
    "刚才在聊什么"这类一次性内容也塞进长期记忆里，越攒越脏。
    """

    content: str = Field(
        min_length=1,
        max_length=CONTENT_MAX_LENGTH,
        description=f"记忆正文，最多 {CONTENT_MAX_LENGTH} 字",
    )
    conversation_id: int | None = Field(
        default=None,
        description="可选：这条记忆是在哪次对话里产生的（仅作追溯，会校验归属）",
    )


class MemoryOut(BaseModel):
    """一条记忆的对外形态。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    is_enabled: bool = Field(
        default=True,
        description="False 表示暂时不拼进提示词（数据仍在，可随时开回来）",
    )
    source_conversation_id: int | None = Field(
        default=None,
        description=(
            "产生这条记忆的会话 id。**可能指向一个已被删除的会话**"
            "（这一列刻意无外键，记忆的寿命比会话长），客户端做跳转时要容错"
        ),
    )
    created_at: datetime
    updated_at: datetime = Field(description="最近一次修改时间 —— 拼提示词时按它倒序")


class MemoryUpdate(BaseModel):
    """改一条记忆。两个字段都可选，但**至少要给一个**。

    ⚠️ 刻意不做"整条替换"（PUT 语义）：改内容时不该被要求把开关也重发一遍，
    反过来也一样。两个都不给属于调用方的 bug，返回 40000 而不是静默成功。
    """

    content: str | None = Field(
        default=None, min_length=1, max_length=CONTENT_MAX_LENGTH,
        description=f"新的记忆正文，最多 {CONTENT_MAX_LENGTH} 字；不传表示不改",
    )
    is_enabled: bool | None = Field(
        default=None, description="是否拼进提示词；不传表示不改"
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "MemoryUpdate":
        if self.content is None and self.is_enabled is None:
            raise ValueError("content 和 is_enabled 至少要提供一个")
        return self
