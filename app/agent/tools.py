"""Agent 可用的工具。

**三件工具，对应三种"取数能力"**：
  1. `search_knowledge` —— 语义检索（按内容找）
  2. `get_document`     —— 按文档名取全文（文档任务：总结 / 对比）
  3. `list_documents`   —— 列可见文档清单（元数据查询："我们都有哪些制度文档"）

为什么不按"任务类型"给工具（summarize / compare / extract 各来一个）：
那三者本质都是"拿到文档内容 + 用不同提示词生成"，做成三个工具只会重叠；
**工具按取数能力切，任务模式交给生成时的提示词**——这才是这套东西的真实分野。

⚠️ 上一版只有一件工具时的注释说"等真的出现第二种能力再加" —— 现在第二种
能力（取指定文档）与第三种（元数据查询）都真的出现了，不是硬凑的，才加的它们。
判断标准很简单：把工具去掉，对应的那类问题就**答不了**（而不是"答得差一点"）。

**给模型看的工具描述**：这份插件的场景里模型是通过 DOCS / LIST 指令选中能力，
再由图里的节点调用这些工具 —— 所以这里不写 JSON schema，
但每个函数的 docstring 都写清了"模型视角下它做什么"。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import STATUS_COMPLETED, Document
from app.models.user import User
from app.repositories import document_chunk_repository, knowledge_repository
from app.services import knowledge_service, vector_service

logger = logging.getLogger(__name__)


def _risk_action(result) -> dict:
    """把确定性巡检结果翻译成可供 Agent/UI 使用的结构化动作草稿。"""
    action_map = {
        "SESSION_HAS_ITEMS": ("add_session_item", False),
        "SKU_ACTIVE": ("review_sku_status", True),
        "INVENTORY_SUFFICIENT": ("refresh_or_restock", False),
        "INVENTORY_FRESH": ("collect_platform_data", False),
        "PROMOTION_ACTIVE": ("review_promotion", False),
        "PRICE_MATCH": ("request_price_fix", True),
        "PRODUCT_MANUAL_VALID": ("upload_product_manual", False),
        "CLAIM_EVIDENCE_VALID": ("upload_claim_evidence", False),
    }
    action_type, requires_approval = action_map.get(
        result.rule_code, ("manual_review", False)
    )
    # “价格不一致”有两种完全不同的业务含义：有计划价时才是平台改价，
    # 没有计划价时只是计划数据没填。后者不能生成改价审批，否则目标值为空。
    planned_price = (result.evidence or {}).get("planned_unit_price")
    if result.rule_code == "PRICE_MATCH" and planned_price is None:
        action_type = "set_planned_price"
        requires_approval = False
    return {
        "risk_id": result.id,
        "rule_code": result.rule_code,
        "action_type": action_type,
        "priority": "high" if result.severity == "blocker" else "medium",
        "requires_approval": requires_approval,
        "sku_id": result.sku_id,
        "title": result.rule_name,
        "description": result.suggestion or result.message,
        "expected_value": (
            {"live_price": planned_price}
            if result.rule_code == "PRICE_MATCH"
            and planned_price is not None
            else None
        ),
    }


def get_live_session_context(db: Session, user: User, session_id: int) -> dict:
    """读取一场直播的实时业务上下文，不执行任何写操作。

    这是直播运营 Agent 的核心业务工具。权限沿用直播场次：管理员可查看全部，
    普通用户只能查看自己负责的场次。返回最新巡检、风险、审批和 RPA 状态，
    避免模型凭自然语言猜测数据库事实。
    """
    # 局部导入避免通用知识工具加载时形成 service 循环依赖。
    from app.models.live_commerce import RULE_FAILED
    from app.repositories import rpa_repository
    from app.services import live_commerce_service

    session = live_commerce_service.get_session(db, user, session_id)
    inspections = live_commerce_service.list_inspections(
        db, user, session_id=session_id
    )
    latest = inspections[0] if inspections else None
    failed = (
        [row for row in latest.results if row.status == RULE_FAILED]
        if latest is not None
        else []
    )
    tasks = rpa_repository.list_tasks_for_session(db, session_id)
    approvals = rpa_repository.list_approvals_for_session(db, session_id)

    risks = [
        {
            "id": row.id,
            "rule_code": row.rule_code,
            "rule_name": row.rule_name,
            "severity": row.severity,
            "sku_id": row.sku_id,
            "message": row.message,
            "suggestion": row.suggestion,
            "evidence": row.evidence or {},
        }
        for row in failed
    ]
    return {
        "session": {
            "id": session.id,
            "title": session.title,
            "status": session.status,
            "starts_at": session.starts_at.isoformat(),
            "item_count": len(session.items),
            "sku_codes": [item.sku.sku_code for item in session.items],
        },
        "inspection": (
            {
                "id": latest.id,
                "status": latest.status,
                "conclusion": latest.conclusion,
                "score": latest.score,
                "blocker_count": latest.blocker_count,
                "warning_count": latest.warning_count,
                "passed_count": latest.passed_count,
                "rule_version": latest.rule_version,
                "completed_at": latest.completed_at.isoformat()
                if latest.completed_at else None,
            }
            if latest is not None
            else None
        ),
        "risks": risks,
        "recommended_actions": [_risk_action(row) for row in failed],
        "approvals": [
            {
                "id": row.id,
                "task_type": row.task_type,
                "sku_id": row.sku_id,
                "status": row.status,
                "expected_value": row.expected_value,
                "task_id": row.task_id,
            }
            for row in approvals
        ],
        "rpa_tasks": [
            {
                "id": row.id,
                "task_type": row.task_type,
                "status": row.status,
                "attempt_count": row.attempt_count,
                "error_message": row.error_message,
                "completed_at": row.completed_at.isoformat()
                if row.completed_at else None,
            }
            for row in tasks
        ],
    }


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


# ---- 第二件工具：按文档名取全文 ----

# 匹配人话里的"文档名"时先剥掉的通用尾缀。
# 用户说《考勤制度》，而库里的文件叫《员工考勤与休假管理制度》——
# 整体做子串匹配是匹配不上的（中间隔着"与休假"），剥掉尾缀拿"考勤"去匹配才行。
_NAME_SUFFIXES = (
    "制度", "规范", "管理", "办法", "规定", "细则", "手册",
    "文件", "标准", "流程", "条例", "指南", "方案",
)


def _strip_suffix(name: str) -> str:
    """剥掉一个通用尾缀（"考勤制度" → "考勤"）。只剥一层，剥不动就原样返回。"""
    core = (name or "").strip()
    for suf in _NAME_SUFFIXES:
        if core.endswith(suf) and len(core) > len(suf):
            return core[: -len(suf)]
    return core


def _document_scope(db: Session, user: User, kb_ids: list[int] | None):
    """文档可见范围 = 用户的库权限 ∩ 请求限定的库。

    返回的 `None` 与检索路径同一语义（**管理员不受限**），`[]` 表示无可见库。
    这两个必须分开 —— 合成一个空列表会把"管理员看全库"变成"什么都看不到"。
    """
    allowed = knowledge_service.get_accessible_kb_ids(db, user)
    if allowed == []:
        return []
    if allowed is None:
        # 管理员：请求带了库范围就按它收窄，没带就是不限
        return kb_ids
    if kb_ids is None:
        return allowed
    return [k for k in kb_ids if k in allowed]


def _match_document(db: Session, keyword: str, scope) -> tuple[Document | None, int]:
    """在可见范围内按文件名找一份文档。

    **只认已完成的文档**（`status == completed`）：处理中 / 处理失败的文档
    命中的是**上一轮的旧切块**（重跑处理期间新旧并存），拿去"总结"会总结出旧内容；
    而且检索侧（`vector_service`）本来就要求 completed，两边口径必须一致 ——
    否则会出现"检索搜不到、文档任务却取到了"的偏差。

    同名多份时的取舍：**先取名字最短的**（最短 = 最接近查询词），
    **同长再取最新上传的**（用户说《管理制度汇编》多半指 2025 版而不是 2024 版；
    原先的次键是 id 升序 = 永远选旧的，是静默的错版本）。

    返回 `(命中的文档, 可见候选数)` —— 候选数 > 1 说明这个名字有歧义，
    由调用方写进轨迹（用户至少有机会知道"有两份，取了新的那份"）。
    """
    stmt = (
        select(Document)
        .where(Document.filename.contains(keyword, autoescape=True))
        .where(Document.status == STATUS_COMPLETED)
        .order_by(
            func.length(Document.filename),
            Document.created_at.desc(),
            Document.id.desc(),
        )
        .limit(5)  # 只看前 5 个候选用于计数；再多也说明不了更多问题
    )
    if scope is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(scope))
    rows = list(db.scalars(stmt))
    return (rows[0] if rows else None), len(rows)


def get_document(
    db: Session,
    user: User,
    name: str,
    kb_ids: list[int] | None = None,
) -> dict:
    """按文档名取**全文**（文档任务：总结 / 对比 / 提取）。

    匹配策略：**逐级剥尾缀 + 逐级试匹配**（都对权限范围内、文件名做匹配）：
      1. 原样子串匹配 —— 用户把文件名说全了；
      2. 没中就把通用尾缀剥掉一层再试 —— "考勤管理制度" → "考勤管理"（还不中）
         → "考勤" → 命中《员工考勤与休假管理制度》。
    逐级试（而不是"剥到底只试一次"）是为了**更早命中**：每剥一层都先试一次，
    剥到哪一级中就以哪一级为准，少让"剥过头"的短词抢到别的文档。

    返回 `{"results": [...], "searched_kb_ids": ..., "matched": int, "candidates": int}`：
    `results` 最多一条（按文档合并成一条，`content` 是全文，不含截断），
    字段与检索结果**同构**，下游拼上下文/生成引用全部复用同一条路。
    取不到时 `results=[]` —— 由调用方决定怎么办（fetch_docs 节点会补检索/回落）。
    """
    keyword = (name or "").strip()
    if not keyword:
        logger.warning("Agent 文档工具收到空名，已跳过")
        return {"results": [], "searched_kb_ids": [], "matched": 0, "candidates": 0}

    scope = _document_scope(db, user, kb_ids)
    if scope == []:
        return {"results": [], "searched_kb_ids": [], "matched": 0, "candidates": 0}

    doc = None
    candidates = 0
    core = keyword
    while doc is None:
        doc, candidates = _match_document(db, core, scope)
        if doc is not None:
            break
        stripped = _strip_suffix(core)
        if stripped == core:
            break  # 剥不动了（没有尾缀可剥 / 已剥到最后一个字符）
        core = stripped

    if doc is None:
        logger.info("文档工具未匹配到文档 name=%r（范围=%s）", keyword[:40], scope)
        return {"results": [], "searched_kb_ids": scope, "matched": 0, "candidates": 0}

    chunks = document_chunk_repository.list_by_document(db, doc.id)
    content = "\n\n".join((c.content or "").strip() for c in chunks if (c.content or "").strip())
    if not content:
        # 文档存在但一个可用的块都没有（还没切块 / 切块全空）——当作没取到
        logger.info("文档工具命中文档但无可用正文 doc_id=%s", doc.id)
        return {"results": [], "searched_kb_ids": scope, "matched": 0, "candidates": candidates}

    logger.info(
        "文档工具命中 name=%r -> doc_id=%s chunks=%s 字数=%s 候选=%s",
        keyword[:40], doc.id, len(chunks), len(content), candidates,
    )
    return {
        "results": [
            {
                # chunk_id 取首块：它是"引用溯源"的入口（点 [n] 能看这块原文）
                "chunk_id": chunks[0].id,
                "document_id": doc.id,
                "document_name": doc.filename,
                "knowledge_base_id": doc.knowledge_base_id,
                "page_number": None,
                # 🔴 文档级命中**没有**"相似度"这个概念 —— 这里是 None 而不是编一个 1.0。
                # 前端对 None 的显示是"整篇文档"；编 1.0 会显示成"相似度 100%"，是假精度。
                "score": None,
                "content": content,
            }
        ],
        "searched_kb_ids": scope,
        "matched": 1,
        "candidates": candidates,
    }


# ---- 第三件工具：文档清单（元数据查询）----

# 清单最多列几份：不是"技术限制"而是**产品判断** —— 几百行的目录谁也读不下去，
# 超出时截断并在答案里说明（truncated=True）。
LIST_MAX = 50


def list_documents(
    db: Session,
    user: User,
    kb_ids: list[int] | None = None,
) -> dict:
    """列出可见范围内的文档**清单**（按知识库分组）。

    用途：用户问"我们都有哪些制度文档 / 库里有什么文件"—— 这是**元数据查询**，
    与"按内容找"（search_knowledge）、"取某一篇全文"（get_document）并列的第三种取数：
    它不产出资料正文，产出的是一份**目录**（由调用方直接呈现，无需模型加工）。

    只列 **completed** 的文档（与检索口径一致：没处理完的取不到内容，
    列出来只会让用户困惑"为什么它有但答不出来"）。
    最多 `LIST_MAX` 份，超出时 `truncated=True`（调用方要在答案里说明还有更多）。
    """
    scope = _document_scope(db, user, kb_ids)
    if scope == []:
        return {"items": [], "truncated": False, "searched_kb_ids": []}

    stmt = (
        select(Document)
        .where(Document.status == STATUS_COMPLETED)
        .order_by(Document.knowledge_base_id, Document.filename)
    )
    if scope is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(scope))
    # 多取一条：用它判断"是否还有更多"（比再 count 一次省一条 SQL）
    rows = list(db.scalars(stmt.limit(LIST_MAX + 1)))
    truncated = len(rows) > LIST_MAX
    rows = rows[:LIST_MAX]

    kb_map = {
        kb.id: kb.name
        for kb in knowledge_repository.list_by_ids(
            db, sorted({d.knowledge_base_id for d in rows})
        )
    }
    items = [
        {
            "document_id": d.id,
            "filename": d.filename,
            "knowledge_base_id": d.knowledge_base_id,
            "knowledge_base_name": kb_map.get(d.knowledge_base_id, "(知识库已删除)"),
        }
        for d in rows
    ]
    return {"items": items, "truncated": truncated, "searched_kb_ids": scope}
