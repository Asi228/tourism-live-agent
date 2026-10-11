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

3. **多轮与落库（Phase 9 起）**：每次问答都会落到 `conversations` / `messages`，
   并把最近 `CONVERSATION_MAX_HISTORY_TURNS` 轮的问答对带进 Prompt。
   注意**检索范围不跟着会话走** —— `kb_ids` 是"这一问想查哪些库"的意图，
   用户随时会改（这次只查员工手册、下次查全部），锁在会话上反而会让改了不生效。

   ⚠️ 已知限制：历史只是**照原样拼进 Prompt**，没有做"指代消解"。
   所以"它的天数是多少"这类省略主语的追问，检索仍然按字面去搜，效果有限。
   要做对需要先让模型把问题改写完整（query rewriting）再检索 —— 那是后续的事。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.rag import llm, prompt
from app.services import conversation_service, memory_service, vector_service

logger = logging.getLogger(__name__)


def _no_hit(
    query: str,
    scope: list[int] | None,
    found: dict | None = None,
    conversation_id: int | None = None,
) -> dict:
    """没有可用资料时的统一返回 —— **不调用大模型**。

    两条路径共用：检索一条都没返回；或返回了但一条都拼不进上下文。
    共用是为了保证两者的响应形状完全一致（前端只看 hit 就够，不用区分）。
    `found` 是检索层的原始返回，用来把"没重排的原因""跳过的孤儿数"一并带出去。

    注意：**没命中也会落进会话历史**（见 answer 末尾）—— 用户问过什么，
    下一轮和刷新后的列表里都该看得到，不能因为"没找到资料"就从记录里消失。
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
        "conversation_id": conversation_id,
    }


def answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
    conversation_id: int | None = None,
) -> dict:
    """一次问答。返回值结构与 schemas/chat.py 的 ChatResponse 对应。

    `conversation_id` 为空时会在本次**隐式新建**一个会话（Phase 9），
    并把新 id 放进响应；前端后续轮次带上它，就能接着上文问。
    """
    top_k = top_k or settings.RAG_TOP_K

    # 会话与历史要在检索**之前**准备好：拿了别人的 conversation_id 就该立刻 404，
    # 不该先白花一次检索和一次生成的价钱。
    conv = conversation_service.ensure_conversation(db, user, conversation_id, query)
    history = conversation_service.load_history(db, conv.id)

    # rerank=True：问答这一侧默认走「多召回 + 精排」（Phase 7）。
    # 是否真的重排还取决于 settings.RAG_RERANK_ENABLED，且重排失败会自动降级 ——
    # 结果里带 reranked 字段，能看出这次到底排没排。
    found = vector_service.search(db, user, query, kb_ids=kb_ids, top_k=top_k, rerank=True)
    results = found["results"]
    scope = found["searched_kb_ids"]

    # 没检索到 → 不调大模型（见文件头第 2 条）
    if not results:
        logger.info("RAG 未命中，跳过 LLM user=%s query=%r", user.username, query[:50])
        return _persist(db, conv, query, _no_hit(query, scope, found, conv.id))

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
        return _persist(db, conv, query, _no_hit(query, scope, found, conv.id))

    # history：最近几轮的问答对（Phase 9）。只带问答本身，不带当时检索到的资料 ——
    # 否则每轮的参考资料都会堆进上下文，token 会随轮数平方级上涨。
    # 长期记忆（Phase 11）与对话摘要（Phase 12）都在**这里**才取：
    # 没命中、没上下文的路径都不该白查一次库。
    memory = memory_service.section_for_prompt(db, user)
    summary = conversation_service.summary_section(conv)
    got = llm.chat(
        prompt.build_system_prompt(memory_section=memory, summary_section=summary),
        prompt.build_user_message(query, context),
        history=history,
    )

    logger.info(
        "RAG 问答完成 user=%s conv=%s 历史=%s轮 summary=%s字 memory=%s字 scope=%s hits=%s "
        "adopted=%s reranked=%s answer_len=%s total_tokens=%s",
        user.username,
        conv.id,
        len(history) // 2,
        len(summary),
        len(memory),
        "ALL" if scope is None else scope,
        len(results),
        len(adopted),
        found["reranked"],
        len(got["answer"]),
        (got["usage"] or {}).get("total_tokens"),
    )
    return _persist(db, conv, query, {
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
        "conversation_id": conv.id,
    })


def _persist(db: Session, conv, query: str, data: dict) -> dict:
    """把这一轮问答写进会话并提交（Phase 9）。

    三条返回路径（未命中 / 上下文为空 / 正常生成）都从这里出去 ——
    它们是"这一问怎么了"，但对会话历史来说都只是**一轮对话**，都该被记下来。

    提交放在这里而不是 repository：事务边界归 service 层（项目硬约定）。
    反过来，如果生成阶段抛了异常，这个函数根本不会被执行 ——
    那个只 flush 未提交的新会话会随 session 关闭自动消失，不会留下空壳。
    """
    conversation_service.append_turn(
        db,
        conv,
        query,
        data["answer"],
        sources=data.get("sources") or [],
        meta={
            # source 让前端回放历史时知道这条是哪个接口答的（纯 RAG 没有轨迹，
            # 渲染方式与 Agent 不同）。两个 service 都写这个字段，口径一致。
            "source": "rag",
            "hit": data.get("hit"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "reranked": data.get("reranked"),
            "rerank_skipped_reason": data.get("rerank_skipped_reason"),
            "skipped_orphans": data.get("skipped_orphans", 0),
            "searched_kb_ids": data.get("searched_kb_ids"),
        },
    )
    db.commit()
    return data
