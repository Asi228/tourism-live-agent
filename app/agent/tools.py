"""Agent 可用的工具。

**最小版本只给一个工具**：知识库检索。

为什么不多给几个：多工具的价值在于"模型需要在几种不同的能力之间做选择"，
而本项目当前只有一种取数能力（检索知识库）—— 硬塞几个同类工具，
只会让提示词更长、决策更容易出错，是典型的"为了用而用"。
等真的出现第二种能力（查数据库、调外部系统）再加，那时候多工具才讲得通。

**检索直接复用 `vector_service.search()`**：权限过滤、空范围直接返回空、
重排失败降级、孤儿向量跳过……全都在那一层解决过了。
Agent 不该另开一条检索路径 —— 否则会出现"RAG 搜得到、Agent 搜不到"的偏差。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.services import vector_service

logger = logging.getLogger(__name__)


def search_knowledge(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
) -> dict:
    """知识库检索工具。

    返回 `{"results": [...], "searched_kb_ids": [...], "reranked": bool, ...}`，
    与 `/api/search` 同构 —— 这样 Agent 的引用来源和纯 RAG 的来源长得一样，
    前端两侧可以复用同一套渲染。
    """
    keyword = (query or "").strip()
    if not keyword:
        # 模型偶尔会给出空查询，直接挡掉：既省一次 embedding，也避免拿到无意义结果
        logger.warning("Agent 检索工具收到空查询，已跳过")
        return {"results": [], "searched_kb_ids": [], "reranked": False, "skipped_orphans": 0}

    return vector_service.search(
        db,
        user,
        keyword,
        kb_ids=kb_ids,
        top_k=top_k or settings.RAG_TOP_K,
        rerank=True,
    )
