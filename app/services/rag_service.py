"""RAG 问答编排：检索 → 拼 Prompt → 生成 → 回答案与引用。

三个刻意的设计（都是 Phase 6 定下的口径）：

1. **检索完全复用 `vector_service.search()`**，不另开一条检索路径。
   权限过滤、空范围直接返回空、孤儿向量跳过……全都在那一层解决过了，
   这里再写一遍只会制造"两条路径行为不一致"的隐患。

2. **没命中就不调大模型**（`hit=False`，直接返回固定提示语）。
   企业知识库的价值是"答案必须来自本公司文档"；把空上下文交给模型，
   它会用通用常识回答（"年假一般 5 天"），员工会当成公司规定 —— 这是幻觉。
   顺带省下一笔完全没必要的 token。

   ⚠️ 实测补充（2026-10-03，真机端到端）：**Milvus 没有相似度阈值**，
   只要用户有可访问的知识库，它总会返回 top_k 条。所以"库里没有这个内容"
   这种情况 `hit` 仍然是 true —— 这条分支真正兜住的是「一个可访问的库都没有」
   （未授权的用户、或登录用户尚无任何授权库）。
   而"检索回来的资料其实不相关"由系统提示词第 3 条兜底：实测问库里没有的
   「公司食堂几点开饭」时，模型明确回答"根据现有资料无法回答该问题"，
   没有拿常识编答案 —— 这正是企业知识库该有的行为。
   （当时最高分 0.5182，而真正相关的两条是 0.7471 / 0.7717。
   要不要加相似度阈值过滤，见交付说明里的待定项。）

3. **不做多轮、不落库**。会话记忆（conversations / messages 表）是 Phase 9 的活，
   那张表的设计要跟历史压缩、user_memories 一起定，现在建了也得推倒重来。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.rag import llm, prompt
from app.services import vector_service

logger = logging.getLogger(__name__)


def _no_hit(query: str, scope: list[int] | None, found: dict | None = None) -> dict:
    """没有可用资料时的统一返回 —— **不调用大模型**。

    两条路径共用：检索一条都没返回；或返回了但一条都拼不进上下文。
    共用是为了保证两者的响应形状完全一致（前端只看 hit 就够，不用区分）。
    `found` 是检索层的原始返回，用来把"没重排的原因""跳过的孤儿数"一并带出去。
    """
    found = found or {}
    return {
        "query": query,
        "answer": prompt.NO_HIT_ANSWER,
        "hit": False,
        "sources": [],
        "searched_kb_ids": scope,
        "model": None,          # 没调用模型，所以是 null 而不是模型名
        "usage": None,
        "finish_reason": None,
        "reranked": False,
        "rerank_skipped_reason": found.get("rerank_skipped_reason"),
        "skipped_orphans": found.get("skipped_orphans", 0),
    }


def answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
) -> dict:
    """一次问答。返回值结构与 schemas/chat.py 的 ChatResponse 对应。"""
    top_k = top_k or settings.RAG_TOP_K

    # rerank=True：问答这一侧默认走「多召回 + 精排」（Phase 7）。
    # 是否真的重排还取决于 settings.RAG_RERANK_ENABLED，且重排失败会自动降级 ——
    # 结果里带 reranked 字段，能看出这次到底排没排。
    found = vector_service.search(db, user, query, kb_ids=kb_ids, top_k=top_k, rerank=True)
    results = found["results"]
    scope = found["searched_kb_ids"]

    # 没检索到 → 不调大模型（见文件头第 2 条）
    if not results:
        logger.info("RAG 未命中，跳过 LLM user=%s query=%r", user.username, query[:50])
        return _no_hit(query, scope, found)

    context, adopted, sources = prompt.build_prompt_and_sources(
        results, settings.RAG_MAX_CONTEXT_CHARS
    )

    # 检索有结果，但一条都拼不进上下文（RAG_MAX_CONTEXT_CHARS 配得过小、
    # 或所有命中的正文都是空白）。**这时绝不能去调大模型** —— 那是拿空资料作答，
    # 白花钱且违背"答案必须来自文档"。config 里已有下限校验，这里是运行时最后一道。
    if not adopted:
        logger.warning(
            "检索到 %s 条但上下文为空，跳过 LLM user=%s RAG_MAX_CONTEXT_CHARS=%s",
            len(results), user.username, settings.RAG_MAX_CONTEXT_CHARS,
        )
        return _no_hit(query, scope, found)

    got = llm.chat(prompt.SYSTEM_PROMPT, prompt.build_user_message(query, context))

    logger.info(
        "RAG 问答完成 user=%s scope=%s hits=%s adopted=%s reranked=%s answer_len=%s total_tokens=%s",
        user.username,
        "ALL" if scope is None else scope,
        len(results),
        len(adopted),
        found["reranked"],
        len(got["answer"]),
        (got["usage"] or {}).get("total_tokens"),
    )
    return {
        "query": query,
        "answer": got["answer"],
        "hit": True,
        "sources": sources,
        "searched_kb_ids": scope,
        "model": settings.LLM_MODEL,
        "usage": got["usage"],
        "finish_reason": got["finish_reason"],
        "reranked": found["reranked"],
        "rerank_skipped_reason": found.get("rerank_skipped_reason"),
        "skipped_orphans": found.get("skipped_orphans", 0),
    }
