"""FAQ 的编排（常见问题置顶）。

权限口径与知识库其他子资源一致：
  · 读（列表）= 库的 **read** 权限
  · 写（增删改）= 库的 **admin** 权限
无权与不存在共用 404，不泄露"这个 id 存在"。

⚠️ **FAQ 的答案刻意不进向量库、不参与检索**：它是"命中即返回"的快捷方式，
与 RAG 是两条独立的路。混进来会让"这个答案来自哪份文档"说不清 ——
而可溯源正是这套系统最值钱的地方。
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.knowledge_base_faq import (
    ANSWER_MAX_LENGTH,
    FAQ_MAX_PER_KB,
    QUESTION_MAX_LENGTH,
    KnowledgeBaseFaq,
)
from app.models.user import User
from app.repositories import faq_repository
from app.services import conversation_service, knowledge_service

logger = logging.getLogger(__name__)


def _require_admin(db: Session, user: User, kb_id: int):
    """把"库的 admin 权限"转成 404 语义（不是 403）。"""
    try:
        return knowledge_service.ensure_kb_permission(
            db, user, kb_id, knowledge_service.PERM_ADMIN
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在或没有管理权限")


def _require_read(db: Session, user: User, kb_id: int):
    try:
        return knowledge_service.ensure_kb_permission(
            db, user, kb_id, knowledge_service.PERM_READ
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在或没有访问权限")


def list_faqs(db: Session, user: User, kb_id: int) -> list[KnowledgeBaseFaq]:
    """某库的 FAQ（有读权限即可）。"""
    _require_read(db, user, kb_id)
    return faq_repository.list_by_kb(db, kb_id)


def create_faq(
    db: Session,
    user: User,
    kb_id: int,
    question: str,
    answer: str,
    sort_order: int = 0,
) -> KnowledgeBaseFaq:
    """新增一条 FAQ。超过 `FAQ_MAX_PER_KB` 直接拒绝，不静默挤掉旧的。"""
    _require_admin(db, user, kb_id)

    q = (question or "").strip()
    a = (answer or "").strip()
    if not q:
        raise AppException(ErrorCode.PARAM_ERROR, "问题不能为空")
    if not a:
        raise AppException(ErrorCode.PARAM_ERROR, "答案不能为空")
    if len(q) > QUESTION_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"问题最多 {QUESTION_MAX_LENGTH} 字"
        )
    if len(a) > ANSWER_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"答案最多 {ANSWER_MAX_LENGTH} 字"
        )

    # 同一个库里的问题**不允许重复**。
    # 表上没有唯一约束（不想为它再加一次迁移），所以在这里查一次 —— 代价是一次
    # 索引查询，换来的是"管理端不会越堆越乱"。重复配置还有个隐患：命中时取哪一条
    # 虽然由 `match_faq` 的 order_by 兜住（确定），但那个"确定"是任意的，
    # 不是"正确"的 —— 两条答案不同时，用户看到的取决于 id 大小，很难解释。
    if db.scalar(
        select(KnowledgeBaseFaq).where(
            KnowledgeBaseFaq.knowledge_base_id == kb_id,
            KnowledgeBaseFaq.question == q,
        )
    ) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "这个知识库里已经有同样的问题了")

    if faq_repository.count_by_kb(db, kb_id) >= FAQ_MAX_PER_KB:
        raise AppException(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"一个知识库最多 {FAQ_MAX_PER_KB} 条常见问题，请先删除一些",
        )

    row = faq_repository.create(db, kb_id, q, a, sort_order, user.id)
    db.commit()
    logger.info("新增 FAQ kb=%s id=%s user=%s", kb_id, row.id, user.username)
    return row


def update_faq(
    db: Session,
    user: User,
    faq_id: int,
    question: str | None = None,
    answer: str | None = None,
    sort_order: int | None = None,
) -> KnowledgeBaseFaq:
    """改一条 FAQ。三个字段都可选，但**至少要给一个**（同 `MemoryUpdate`）。"""
    if question is None and answer is None and sort_order is None:
        raise AppException(ErrorCode.PARAM_ERROR, "没有需要修改的内容")

    row = faq_repository.get_by_id(db, faq_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "常见问题不存在")
    _require_admin(db, user, row.knowledge_base_id)

    if question is not None:
        q = (question or "").strip()
        if not q:
            raise AppException(ErrorCode.PARAM_ERROR, "问题不能为空")
        if len(q) > QUESTION_MAX_LENGTH:
            raise AppException(
                ErrorCode.PARAM_ERROR, f"问题最多 {QUESTION_MAX_LENGTH} 字"
            )
        row.question = q
    if answer is not None:
        a = (answer or "").strip()
        if not a:
            raise AppException(ErrorCode.PARAM_ERROR, "答案不能为空")
        if len(a) > ANSWER_MAX_LENGTH:
            raise AppException(
                ErrorCode.PARAM_ERROR, f"答案最多 {ANSWER_MAX_LENGTH} 字"
            )
        row.answer = a
    if sort_order is not None:
        row.sort_order = sort_order

    db.commit()
    logger.info("修改 FAQ id=%s user=%s", faq_id, user.username)
    return row


def delete_faq(db: Session, user: User, faq_id: int) -> None:
    """删一条 FAQ。**不可逆**。"""
    row = faq_repository.get_by_id(db, faq_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "常见问题不存在")
    _require_admin(db, user, row.knowledge_base_id)

    db.delete(row)
    db.commit()
    logger.info("删除 FAQ id=%s user=%s", faq_id, user.username)


# ==================== 命中即返回（问答入口的第一道闸门）====================
#
# 这一段的定位：**问答链路之前的一道闸门**，不属于 RAG、也不属于 Agent ——
# 两条路都在进入自己的流程前先问一句"这句话有没有现成的标准答案"。
#
# 为什么值得单独做一层：
#   · **统一口径**：同一个制度问题让模型答十次可能十种措辞，而 FAQ 只有一份；
#   · **省一次模型调用**：命中即返回，检索与生成全都省掉；
#   · **避免"一句话两个答案"**：卡片的提示答案与正文答案不一致时，用户不知道信谁。

# 写进 `messages.meta.answer_mode` 与响应体，前端据此显示"来自常见问题"标签
ANSWER_MODE_FAQ = "faq"


def match_faq(
    db: Session, user: User, query: str, kb_ids: list[int] | None = None
) -> KnowledgeBaseFaq | None:
    """在**当前用户有权访问、且在本次检索范围内**的库里找一条问题完全相同的 FAQ。

    ⚠️ **判据是精确匹配（去掉首尾空白后相等），刻意不做模糊匹配。**
    模糊匹配在"制度问答"这个场景里误命中的代价很高：用户问"年假能不能延到下一年"，
    被模糊命中到"年假几天"的标准答案，他拿到的是一个**看着权威、其实答非所问**的答案 ——
    比走一趟 RAG 差得多。宁可漏，不可错。

    `kb_ids` 是本次请求限定的检索范围（`None` = 用户所有可访问的库）。
    它与实际权限**取交集** —— 传进来的 id 不可信（与 `/api/search` 同一口径）。
    """
    text = (query or "").strip()
    if not text:
        return None

    allowed = knowledge_service.get_accessible_kb_ids(db, user)  # None = 管理员不受限
    if kb_ids:
        scoped = list(kb_ids) if allowed is None else [k for k in kb_ids if k in allowed]
    else:
        scoped = allowed  # None 继续表示"不受限"

    if scoped is not None and not scoped:
        return None  # 没有可访问的库 → 不可能命中

    stmt = select(KnowledgeBaseFaq).where(KnowledgeBaseFaq.question == text)
    if scoped is not None:
        stmt = stmt.where(KnowledgeBaseFaq.knowledge_base_id.in_(scoped))
    # 同一句话在多个库里都配了时，取库 id 最小的那个 —— 至少要**确定**，
    # 不能让 MySQL 的默认顺序决定用户看到哪一条。
    stmt = stmt.order_by(KnowledgeBaseFaq.knowledge_base_id, KnowledgeBaseFaq.id).limit(1)
    return db.scalar(stmt)


def answer_from_faq(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    conversation_id: int | None = None,
) -> dict | None:
    """命中 FAQ 就直接给出标准答案；**没命中返回 `None`**（调用方继续走 RAG / Agent）。

    返回结构与 `rag_service.answer()` 对齐（同一套前端渲染），差异只在：
      · `sources` 恒为空 —— 答案来自 FAQ 表，不是文档片段，所以没有可溯源的引用；
      · `answer_mode = "faq"` —— 前端据此显示来源标签、并关掉引用区；
      · `faq_id` —— 便于后续做"这条 FAQ 被命中过几次"的统计。

    `hit=True` 是**诚实的**：这次确实有依据（管理员写的标准答案），只是依据不是文档。
    真正的"没依据"是 no_evidence 那种。

    ⚠️ **曾经是已知边界、现已修复（2026-10-15）**：回放脱敏过去只看 `sources`，
    而 FAQ 命中的 `sources` 是空的 —— 用户日后失去该库权限时，这条 FAQ 答案不会
    像文档引用那样被隐藏。修法：落库 meta 带 `faq_kb_id`（答案真正出自哪个库），
    `_cites_revoked_kb` 的判据并入它。
    ⚠️ 别为了省事去判 `meta.searched_kb_ids`：普通 RAG 路径里它是"检索范围"而不是
    "答案引用了哪些库"（在三个库里搜、只命中一个，另两个 later 撤权不该连坐），
    FAQ 这条路才敢把"来源库"单独记。
    """
    faq = match_faq(db, user, query, kb_ids)
    if faq is None:
        return None

    started = time.perf_counter()
    conv = conversation_service.ensure_conversation(db, user, conversation_id, query)

    data = {
        "query": query,
        "answer": faq.answer,
        "hit": True,
        "answer_mode": ANSWER_MODE_FAQ,
        "sources": [],
        "searched_kb_ids": [faq.knowledge_base_id],
        # 答案真正出自哪个库 —— 回放脱敏的判据（见函数 docstring 的修复说明）。
        # 与 searched_kb_ids 在这里值恰好相同，但语义不同：它永远指向"这一个库"。
        "faq_kb_id": faq.knowledge_base_id,
        "faq_id": faq.id,
        # 没调模型 —— 这几个字段是 null 而不是伪造一个模型名/用量
        "model": None,
        "usage": None,
        "finish_reason": None,
        "reranked": False,
        "rerank_skipped_reason": None,
        "skipped_orphans": 0,
        "conversation_id": conv.id,
        # ⚠️ 下面三个是 **Agent 那条路的响应必填字段**（`AgentChatResponse`）。
        # 两条问答路径共用这一个构造函数，所以这里补齐让形状兼容：
        #   · iterations=0 —— 真话：一次检索都没做（不是"查了 0 轮"）
        #   · steps=[]     —— 没有多步决策，前端"执行轨迹"面板因此自然不出现
        # 非 Agent 侧（`ChatResponse`）没声明这两个字段，会被 response_model 忽略，无害。
        "iterations": 0,
        "steps": [],
    }

    conversation_service.append_turn(
        db,
        conv,
        query,
        faq.answer,
        sources=[],
        meta={
            "source": ANSWER_MODE_FAQ,
            "answer_mode": ANSWER_MODE_FAQ,
            "hit": True,
            "faq_id": faq.id,
            "model": None,
            "usage": None,
            "finish_reason": None,
            "searched_kb_ids": [faq.knowledge_base_id],
            # 撤权脱敏判据：FAQ 没有 sources，答案出自哪个库全靠它
            "faq_kb_id": faq.knowledge_base_id,
        },
    )
    db.commit()
    data["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    logger.info(
        "FAQ 命中 user=%s faq_id=%s kb=%s conv=%s 耗时=%sms",
        user.username, faq.id, faq.knowledge_base_id, conv.id, data["elapsed_ms"],
    )
    return data
