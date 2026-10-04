"""向量检索的请求 / 响应模型。"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="查询内容")
    kb_ids: list[int] | None = Field(
        default=None,
        description="限定在这些知识库内检索；不传则用当前用户所有可访问的库。"
                    "无论传什么，都会与用户实际权限取交集。",
    )
    top_k: int = Field(default=5, ge=1, le=50, description="返回条数")
    rerank: bool = Field(
        default=False,
        description="是否启用重排。默认关（纯向量检索）；开成 true 可以直观对比"
                    "「重排前 vs 重排后」的排序差异。问答接口 /api/chat 是默认开启的。",
    )


class SearchResultItem(BaseModel):
    chunk_id: int
    document_id: int
    document_name: str
    knowledge_base_id: int
    page_number: int | None = None
    score: float = Field(description="排序分数：默认是余弦相似度；重排后是 reranker 的相关性分数")
    vector_score: float | None = Field(
        default=None,
        description="重排前的向量余弦相似度。仅当本次启用了重排才有值 —— "
                    "留着它是为了能直接看出「重排把谁提到了前面」",
    )
    content: str


class SearchResponse(BaseModel):
    query: str
    top_k: int
    # 本次实际参与检索的知识库 id；None 表示系统管理员（不受限）
    searched_kb_ids: list[int] | None = None
    reranked: bool = Field(
        default=False,
        description="本次结果是否经过重排。false 时具体原因看 rerank_skipped_reason",
    )
    rerank_skipped_reason: str | None = Field(
        default=None,
        description="没重排的原因：disabled=本次未启用（含全局开关关闭）／"
                    "no_scope=用户没有任何可访问的知识库／single_candidate=候选不足 2 条、排序无意义／"
                    "failed=重排服务失败，已降级为向量分数顺序（日志里有 warning）。"
                    "reranked=true 时为 null",
    )
    skipped_orphans: int = Field(
        default=0,
        description="因为命中的是孤儿向量（Milvus 里还在、MySQL 里已不存在）而被跳过的条数。"
                    "**不为 0 说明结果被吃掉了几条** —— 通常是有一次删库没清干净，"
                    "可以用 GET /api/vector/health 查看孤儿、POST /api/vector/purge-orphans 清理",
    )
    candidate_k: int = Field(
        default=0, description="向量检索实际召回的候选条数（开启重排时大于 top_k）"
    )
    results: list[SearchResultItem]


class VectorizeResult(BaseModel):
    """向量化结果回执。"""

    collection: str
    vectorized: int = Field(description="本次写入 Milvus 的向量条数")
