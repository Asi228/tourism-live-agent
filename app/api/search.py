"""向量检索接口。

Phase 5 的出口。Phase 6 的 RAG 会直接复用 `vector_service.search()` 作为检索环节，
所以这个接口和 RAG 走的是**同一条检索路径** —— 不会出现"接口能搜到、RAG 搜不到"的偏差。

权限说明：请求体里的 kb_ids 只是"缩小范围"的意图，真实范围由
`vector_service._resolve_search_scope()` 与用户实际权限求交集后决定。
未授权的库即使写进 kb_ids 也不会被检索。

响应结构用 `response_model` 显式声明（而不是让 service 返回裸 dict）：
Swagger 里能看到字段结构，而且 schema 和真实响应之间不会静默漂移。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.search import SearchRequest, SearchResponse
from app.services import vector_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["检索"])


@router.post(
    "",
    response_model=ApiResponse[SearchResponse],
    summary="向量检索（结果按当前用户权限过滤）",
)
def vector_search(
    payload: SearchRequest,
    _: None = Depends(rate_limit("search")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = vector_service.search(
        db,
        current_user,
        query=payload.query,
        kb_ids=payload.kb_ids,
        top_k=payload.top_k,
        rerank=payload.rerank,
    )
    return success(data)
