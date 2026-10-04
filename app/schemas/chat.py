"""RAG 问答的请求 / 响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="用户问题")
    kb_ids: list[int] | None = Field(
        default=None,
        description="限定在这些知识库内检索；不传则用当前用户所有可访问的库。"
                    "无论传什么，都会与用户实际权限取交集（与 /api/search 同一套逻辑）。",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="检索条数，不传则用服务端配置 RAG_TOP_K。",
    )


class SourceItem(BaseModel):
    """一条引用来源。

    index 与答案里出现的 [1][2] 编号**一一对应** —— 只包含真正拼进 Prompt 的资料，
    被上下文长度截掉的不会出现在这里（否则编号会错位）。
    """

    index: int = Field(description="引用编号，对应答案中的 [index]")
    chunk_id: int
    document_id: int
    document_name: str
    knowledge_base_id: int
    page_number: int | None = Field(default=None, description="PDF 的页码；txt/md 为 null")
    score: float = Field(description="余弦相似度，越大越相关")
    snippet: str = Field(description="正文摘要，便于前端展示引用")


class ChatUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    query: str
    answer: str
    hit: bool = Field(
        description="检索是否返回了结果。false 时**不会调用大模型**，直接返回固定提示语。"
                    "注意：它是「检索层有没有拿到东西」，不等于「资料真的回答了问题」——"
                    "只要用户有可访问的知识库，Milvus 总会返回 top_k 条（它没有相似度阈值），"
                    "所以「库里没这个内容」时 hit 仍是 true，改由模型照提示词回「资料不足」。"
    )
    sources: list[SourceItem]
    searched_kb_ids: list[int] | None = Field(
        default=None, description="本次实际参与检索的知识库 id；null 表示系统管理员（不受限）"
    )
    model: str | None = Field(default=None, description="未命中时为 null（没有调用模型）")
    usage: ChatUsage | None = None
    finish_reason: str | None = None
    reranked: bool = Field(
        default=False,
        description="检索结果是否经过重排（Phase 7）。false 时原因看 rerank_skipped_reason",
    )
    rerank_skipped_reason: str | None = Field(
        default=None,
        description="没重排的原因：disabled=本次未启用（含全局开关关闭）／no_scope=没有可访问的知识库／"
                    "single_candidate=候选不足 2 条／failed=重排服务失败已降级。reranked=true 时为 null",
    )
    skipped_orphans: int = Field(
        default=0, description="因命中孤儿向量而被跳过的条数（不为 0 说明检索结果被吃掉了几条）"
    )
