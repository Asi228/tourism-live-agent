"""概览统计的响应模型。

为什么要给它专门写模型：这个接口的数据是 repository **手拼的 dict**，
没有模型钉着，字段名写错（`Document.kb_id` 那次）只能等它在线上炸成 500。
`response_model` 让"仓库拼出来的形状"和"前端读的键"之间有一处静态约束 ——
形状不对，测试和启动阶段就会红，而不是用户先看到。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class OverviewScope(BaseModel):
    kb_count: int = Field(description="我有权访问的知识库数量")
    unlimited: bool = Field(description="管理员是「不受限」，不是「有 N 个库」")


class OverviewDocuments(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


class OverviewQuestions(BaseModel):
    total: int = Field(description="累计提问数（只算自己的）")
    today: int = Field(description="今日提问数")


class RecentDocument(BaseModel):
    """键名与 `DocumentOut` 对齐。"""

    id: int
    filename: str
    status: str
    knowledge_base_id: int
    chunk_count: int
    created_at: datetime


class RecentQuestion(BaseModel):
    conversation_id: int
    conversation_title: str
    query: str
    created_at: datetime


class OverviewAdmin(BaseModel):
    user_count: int
    # Milvus 不可用时是 null —— 概览是入口页，一个次要指标不该让整页打不开
    vector_count: int | None = None
    unlimited_scope: bool


class OverviewOut(BaseModel):
    scope: OverviewScope
    documents: OverviewDocuments
    questions: OverviewQuestions
    recent_documents: list[RecentDocument] = Field(default_factory=list)
    recent_questions: list[RecentQuestion] = Field(default_factory=list)
    # 非管理员是 null（不是 0）—— 前端据此整块不渲染，避免显示"用户 0 人"的假数字
    admin: OverviewAdmin | None = None


class TrendPoint(BaseModel):
    """趋势里的一个点（一天一个）。"""

    date: str = Field(description="ISO 日期，如 2026-10-12")
    count: int


class KbDocCount(BaseModel):
    """一个知识库的文档数。"""

    knowledge_base_id: int
    count: int


class TrendsOut(BaseModel):
    """概览页图表用的趋势数据。

    与 `overview` **分成两个接口**：首屏只该拿要显示的数字，图表数据按需再取。
    尤其 `question_trend` 里补了一堆 count=0 的点（没有提问的日子也要有，
    否则折线断格），对首屏来说是纯噪音。
    """

    days: int = Field(description="question_trend 覆盖的天数")
    question_trend: list[TrendPoint] = Field(default_factory=list)
    documents_by_kb: list[KbDocCount] = Field(default_factory=list)
