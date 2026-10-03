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


class SearchResultItem(BaseModel):
    chunk_id: int
    document_id: int
    document_name: str
    knowledge_base_id: int
    page_number: int | None = None
    score: float = Field(description="余弦相似度，越大越像")
    content: str


class SearchResponse(BaseModel):
    query: str
    top_k: int
    # 本次实际参与检索的知识库 id；None 表示系统管理员（不受限）
    searched_kb_ids: list[int] | None = None
    results: list[SearchResultItem]


class VectorizeResult(BaseModel):
    """向量化结果回执。"""

    collection: str
    vectorized: int = Field(description="本次写入 Milvus 的向量条数")
