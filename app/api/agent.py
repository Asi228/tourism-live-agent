"""Agent 问答接口（Phase 8）。

与 `/api/chat` 并存，不是替代关系：
  · `/api/chat`  = 纯 RAG（检索 → 生成），一步到位、便宜、可预测
  · `/api/agent/chat` = 多步决策（判定要不要检索 → 检索 → 判断够不够 → 可能再检索 → 生成）

保留两条路径是刻意的：**能直接把"Agent 比纯 RAG 强在哪、多花了多少 token"对比出来**。
如果只留一条，"该不该用 Agent"就永远是个说不清的问题。

同样的取舍在 `/agent/chat` 内部又来了一次 —— 两条：

  POST /chat          非流式，一次返回整段（统一信封 `{code, message, data}`）
  POST /chat/stream   流式（SSE），节点级进度 —— 见 `agent_service.stream_answer`

并存的理由同上：流式不是替代，是为了让"它为什么慢"能看见。Agent 的答案要跑
analyze / retrieve / evaluate / generate 好几步（可能好几轮），非流式时用户只能
盯着一个转圈猜测它在干什么；流式的价值是把**多步决策的进度**吐出来。

⚠️ 这里**刻意不逐字流答案**：README 早就定了口径 —— Agent 的价值在"多步决策的
进度"而不在"一个字一个字地蹦"。要逐字得给 generate 节点注入一条输出通道，
复杂度再上一档，留给后续。
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
from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services import agent_service, faq_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post(
    "/chat",
    response_model=ApiResponse[AgentChatResponse],
    summary="知识库 Agent 问答（多步决策，返回执行轨迹）",
)
def agent_chat(
    payload: AgentChatRequest,
    # 走 chat 那档配额：一次 Agent 问答会调 2~4 次大模型，比纯 RAG 更贵，
    # 所以它绝不能被排除在闸门之外。
    _: None = Depends(rate_limit("chat")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 🔴 FAQ 闸门**与 /api/chat 同一处**：命中即给标准答案，不进 LangGraph。
    # 这道闸门刻意放在两条路各自的入口，而不是塞进 RAG 或 Agent 内部 ——
    # 它是"问答入口"这一层的事，两条路都该先过它（见 faq_service 里那段的说明）。
    faq_data = faq_service.answer_from_faq(
        db, current_user,
        query=payload.query, kb_ids=payload.kb_ids,
        conversation_id=payload.conversation_id,
    )
    if faq_data is not None:
        return success(faq_data, "来自常见问题（标准答案，未调用大模型）")

    data = agent_service.answer(
        db,
        current_user,
        query=payload.query,
        kb_ids=payload.kb_ids,
        top_k=payload.top_k,
        conversation_id=payload.conversation_id,
    )
    message = "检索 %s 轮" % data["iterations"] if data["hit"] else "未使用知识库资料"
    return success(data, message)


@router.post("/chat/stream", summary="知识库 Agent 问答（SSE 流式，节点级进度）")
def agent_chat_stream(
    payload: AgentChatRequest,
    # 同样走 chat 那档配额（理由见上面的 /chat）
    _: None = Depends(rate_limit("chat")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """流式 Agent 问答 —— 事件协议见 `app/services/agent_service.py` 的「流式（SSE）」段。

    ⚠️ 与 `/api/chat/stream` 同款：**这个接口不是"总是返回 SSE"**，两种情况返回 JSON 信封：

      ① **失败** —— "Agent 未启用"、越权、配额到顶，发生在**响应开始之前**；
      ② **FAQ 命中**（成功）—— 答案是现成文本，没有"节点级进度"可言，不开流。

    HTTP 仍 200 + `{code: 40400, …}` / `{code: 0, …}`。前端必须先判 `code === 0`
    再决定是成功信封还是失败，详见 `frontend/src/api/stream.js`。
    只有"多步推理已经开始"之后的失败才降级成 `event: error`。

    没有 `response_model`：响应体不是那个统一信封，`StreamingResponse` 也不接受模型校验。
    """
    # 🔴 FAQ 闸门（与 /agent/chat、/chat/stream 三处一致）
    faq_data = faq_service.answer_from_faq(
        db, current_user,
        query=payload.query, kb_ids=payload.kb_ids,
        conversation_id=payload.conversation_id,
    )
    if faq_data is not None:
        return success(faq_data, "来自常见问题（标准答案，未调用大模型）")

    # `stream_answer` 是**普通函数**（不是生成器）：准备阶段在这里就跑完，
    # 所以上面的异常会正常向上抛、被全局处理器接住。写成生成器会把准备推迟到
    # "响应已开始"之后，越权就只能变成一个 200 流里的事件 —— 那是降级（P13 已踩定）。
    events = agent_service.stream_answer(
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
