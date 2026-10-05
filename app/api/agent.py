"""Agent 问答接口（Phase 8）。

与 `/api/chat` 并存，不是替代关系：
  · `/api/chat`  = 纯 RAG（检索 → 生成），一步到位、便宜、可预测
  · `/api/agent/chat` = 多步决策（判定要不要检索 → 检索 → 判断够不够 → 可能再检索 → 生成）

保留两条路径是刻意的：**能直接把"Agent 比纯 RAG 强在哪、多花了多少 token"对比出来**。
如果只留一条，"该不该用 Agent"就永远是个说不清的问题。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services import agent_service

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
