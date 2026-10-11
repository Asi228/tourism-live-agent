"""RAG 问答接口。

Phase 6 的出口：问题进、答案 + 引用出。
检索环节直接复用 `vector_service.search()`，与 `/api/search` 走**同一条路径**。

两个端点**并存**（不是用一个开关去切）：

  POST /chat          非流式，一次返回整段（统一信封 `{code, message, data}`）
  POST /chat/stream   流式（SSE），逐字返回

并存的理由与 `/api/chat` vs `/api/agent/chat` 一样：能直接对比两条路的行为，
现有调用方与测试也一行不用改。两条路共用 `rag_service._prepare()`，
所以"检索到哪些资料、要不要调模型"的判断**只有一份**。
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit
from app.api.sse import SSE_HEADERS, iter_sse
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


@router.post("/stream", summary="知识库问答（SSE 流式，逐字返回）")
def chat_stream(
    payload: ChatRequest,
    _: None = Depends(rate_limit("chat")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """流式问答 —— 事件协议见 `app/services/rag_service.py` 的「流式（SSE）」段。

    没有 `response_model`：响应体不是那个统一信封，`StreamingResponse` 也不接受模型校验。

    ⚠️ **这个接口不是"总是返回 SSE"**。越权、配额到顶、限流、Milvus 挂了这些
    失败发生在**响应开始之前**（`rag_service.stream_answer` 里的 `_prepare`），
    它们仍然走统一信封：HTTP 200 + `{code: 40400, …}`，`content-type` 是 JSON。
    只有"已经开始吐字之后"的失败才降级成 `event: error`（那时状态码已经发出去了）。
    前端必须按 `content-type` 分流 —— 详见 `frontend/src/api/stream.js`。
    """
    # 注意 `stream_answer` 是**普通函数**（不是生成器）：它在这里就把准备阶段跑完，
    # 所以上面那些异常会正常向上抛、被全局处理器接住。若把它写成生成器，
    # 准备阶段会被推迟到"响应已开始"，越权就只能变成一个 200 流里的事件 —— 那是降级。
    events = rag_service.stream_answer(
        db,
        current_user,
        query=payload.query,
        kb_ids=payload.kb_ids,
        top_k=payload.top_k,
        conversation_id=payload.conversation_id,
    )
    return StreamingResponse(
        iter_sse(events), media_type="text/event-stream", headers=SSE_HEADERS
    )
