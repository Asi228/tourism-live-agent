"""RAG 问答接口。

Phase 6 的出口：问题进、答案 + 引用出。
检索环节直接复用 `vector_service.search()`，与 `/api/search` 走**同一条路径**。

非流式：一次返回整段答案。
流式（SSE）留到前端阶段再做 —— 它的收益全在界面上，而正式前端还没开始；
现在加只会让调试台不好验证，而且检索/拼 Prompt/生成这三步是完全可复用的。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import rag_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["问答"])


@router.post(
    "",
    response_model=ApiResponse[ChatResponse],
    summary="知识库问答（RAG，答案带引用来源）",
)
def chat(
    payload: ChatRequest,
    # 限流放最前面：它比后续的检索/生成便宜得多，应当最先拦。
    # 配额是最紧的一档 —— 一次问答要打 rerank + LLM 两个付费上游。
    _: None = Depends(rate_limit("chat")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = rag_service.answer(
        db,
        current_user,
        query=payload.query,
        kb_ids=payload.kb_ids,
        top_k=payload.top_k,
        conversation_id=payload.conversation_id,
    )
    if not data["hit"]:
        # 把"没命中"这件事在 message 里说清楚：
        # 前端一眼能看出这次**没有调用大模型**（也就没有产生费用）
        return success(data, "未检索到相关资料，未调用大模型")
    return success(data)
