"""Agent 可用的工具。

**两件工具，对应两种"取数能力"**：
  1. `search_knowledge` —— 语义检索（按内容找）
  2. `get_document`     —— 按文档名取全文（文档任务：总结 / 对比）

为什么不按"任务类型"给工具（summarize / compare / extract 各来一个）：
那三者本质都是"拿到文档内容 + 用不同提示词生成"，做成三个工具只会重叠；
**工具按取数能力切，任务模式交给生成时的提示词**——这才是这套东西的真实分野。

⚠️ 上一版只有一件工具时的注释说"等真的出现第二种能力再加" —— 现在第二种
能力（取指定文档）真的出现了，不是硬凑的，才加的它。

**给模型看的工具描述**：这份插件的场景里模型是通过 DOCS 指令点名文档，
再由图里的 fetch_docs 节点调用本工具 —— 所以这里不写 JSON schema，
但每个函数的 docstring 都写清了"模型视角下它做什么"。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import Document
from app.models.user import User
from app.repositories import document_chunk_repository
from app.services import knowledge_service, vector_service

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


def _match_document(db: Session, keyword: str, scope) -> Document | None:
    """在可见范围内按文件名找一份文档。同名多份时取**名字最短**的
    （最短 = 最接近查询词，通常就是用户指的那一份）。"""
    stmt = (
        select(Document)
        .where(Document.filename.contains(keyword, autoescape=True))
        .order_by(func.length(Document.filename), Document.id)
        .limit(1)
    )
    if scope is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(scope))
    return db.scalars(stmt).first()


def get_document(
    db: Session,
    user: User,
    name: str,
    kb_ids: list[int] | None = None,
) -> dict:
    """按文档名取**全文**（文档任务：总结 / 对比 / 提取）。

    匹配策略两步（都对权限范围内、文件名做匹配）：
      1. 原样子串匹配 —— 用户把文件名说全了；
      2. 剥掉通用尾缀再匹配 —— "考勤制度" → "考勤" → 命中《员工考勤与休假管理制度》。

    返回 `{"results": [...], "searched_kb_ids": ..., "matched": int}`：
    `results` 最多一条（按文档合并成一条，`content` 是全文，不含截断），
    字段与检索结果**同构**，下游拼上下文/生成引用全部复用同一条路。
    取不到时 `results=[]` —— 由调用方决定怎么办（fetch_docs 节点会回落检索）。
    """
    keyword = (name or "").strip()
    if not keyword:
        logger.warning("Agent 文档工具收到空名，已跳过")
        return {"results": [], "searched_kb_ids": [], "matched": 0}

    scope = _document_scope(db, user, kb_ids)
    if scope == []:
        return {"results": [], "searched_kb_ids": [], "matched": 0}

    doc = _match_document(db, keyword, scope)
    if doc is None:
        core = _strip_suffix(keyword)
        if core != keyword:
            doc = _match_document(db, core, scope)
    if doc is None:
        logger.info("文档工具未匹配到文档 name=%r（范围=%s）", keyword[:40], scope)
        return {"results": [], "searched_kb_ids": scope, "matched": 0}

    chunks = document_chunk_repository.list_by_document(db, doc.id)
    content = "\n\n".join((c.content or "").strip() for c in chunks if (c.content or "").strip())
    if not content:
        # 文档存在但一个可用的块都没有（还没切块 / 切块全空）——当作没取到
        logger.info("文档工具命中文档但无可用正文 doc_id=%s", doc.id)
        return {"results": [], "searched_kb_ids": scope, "matched": 0}

    logger.info(
        "文档工具命中 name=%r -> doc_id=%s chunks=%s 字数=%s",
        keyword[:40], doc.id, len(chunks), len(content),
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
    }
