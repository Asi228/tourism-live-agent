"""FAQ 的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.knowledge_base_faq import (
    ANSWER_MAX_LENGTH,
    QUESTION_MAX_LENGTH,
)


class FaqOut(BaseModel):
    """一条常见问题。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    question: str
    answer: str
    sort_order: int = 0
    created_by: int
    created_at: datetime
    updated_at: datetime


class FaqCreate(BaseModel):
    """新增一条 FAQ。"""

    knowledge_base_id: int
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LENGTH)
    answer: str = Field(min_length=1, max_length=ANSWER_MAX_LENGTH)
    sort_order: int = Field(default=0, ge=0, description="小的在前")


class FaqUpdate(BaseModel):
    """改一条 FAQ。三个字段都可选，但**至少要给一个** ——
    都不给是调用方的 bug，返回 40000 而不是静默成功（同 `MemoryUpdate`）。"""

    question: str | None = Field(default=None, min_length=1, max_length=QUESTION_MAX_LENGTH)
    answer: str | None = Field(default=None, min_length=1, max_length=ANSWER_MAX_LENGTH)
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> "FaqUpdate":
        if self.question is None and self.answer is None and self.sort_order is None:
            raise ValueError("question / answer / sort_order 至少要提供一个")
        return self
