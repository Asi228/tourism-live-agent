"""向量化与向量检索的编排。

职责边界：
  - 本文件不直接碰 Milvus 客户端，只调 `rag/vector_store.py` 的接口
  - 本文件不直接碰 HTTP，只调 `rag/embedding.py`
  - **权限过滤在这里算，并且是"在检索阶段过滤"**：
    先用 knowledge_service.get_accessible_kb_ids() 算出允许的 kb_id，
    再把它们作为 filter 表达式交给 Milvus —— 而不是先把全部向量捞回来再筛。

与文档处理的关系：
  向量化是**独立的一步**，不在 `process_document()` 的后台任务里自动做。
  原因：向量化依赖外部服务（硅基流动 API + Milvus）。
  如果塞进文档处理流程，那么 Milvus 一挂，"上传文档"这个功能就整个不可用了，
  而且 Phase 4 的文档测试会被迫去打真实外部接口。
  拆开之后：文档处理只依赖本地文件系统，向量化失败可以单独重试，互不影响。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.document import STATUS_COMPLETED
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.rag import embedding, keyword, reranker, vector_store
from app.rag.fusion import rrf_merge
from app.repositories import (
    document_chunk_repository,
    document_repository,
    knowledge_repository,
)
from app.services import knowledge_service

logger = logging.getLogger(__name__)

NO_ACCESS_MSG = "文档不存在或无权访问"


# ==================== 向量化 ====================


def _embed_chunks(chunks: list[DocumentChunk], kb_id: int) -> list[vector_store.ChunkVector]:
    """把一批 chunk 转成待写入 Milvus 的行。

    chunk 表里没有 knowledge_base_id（它挂在 document 上），
    所以 kb_id 由调用方传进来 —— 这也正是 Milvus 侧能做权限过滤的前提。
    """
    vectors = embedding.embed_texts([c.content for c in chunks])
    return [
        vector_store.ChunkVector(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            knowledge_base_id=kb_id,
            page_number=chunk.page_number,
            vector=vector,
        )
        for chunk, vector in zip(chunks, vectors)
    ]


def vectorize_document(db: Session, user: User, document_id: int) -> dict:
    """把一份文档的全部 chunk 向量化并写入 Milvus。

    向量化会写 Milvus，所以按"写操作"要求 -- 需要对该知识库有 write 权限。
    """
    doc = document_repository.get_by_id(db, document_id)
    if doc is None:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    try:
        knowledge_service.ensure_kb_permission(
            db, user, doc.knowledge_base_id, knowledge_service.PERM_WRITE
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    if doc.status != STATUS_COMPLETED:
        # 切分块还没生成就向量化，只会得到空结果，不如直接说清楚
        raise AppException(
            ErrorCode.PARAM_ERROR,
            f"文档尚未处理完成（当前状态 {doc.status}），请等状态变为 completed 再向量化",
        )

    chunks = document_chunk_repository.list_by_document(db, doc.id)
    if not chunks:
        raise AppException(ErrorCode.PARAM_ERROR, "该文档没有切分块，无法向量化")

    vector_store.ensure_collection()
    # 写入前先按 document_id 清一遍旧向量。
    # 为什么必须先删：Milvus 的 upsert 只覆盖**主键相同**的行，而主键是 chunk_id。
    # 如果这份文档重新处理过，MySQL 里已经是一批新的 chunk_id，旧 id 的向量不会被覆盖，
    # 会留下来变成孤儿（检索命中后 _hydrate 取不到正文而被丢弃，白占 top_k 名额）。
    # 这一层同时兜住了「上次向量化时 Milvus 不可用、只写了一部分」的情况。
    vector_store.delete_by_document(doc.id)
    rows = _embed_chunks(chunks, doc.knowledge_base_id)
    written = vector_store.upsert(rows)
    flushed = vector_store.flush() if written else True   # 理由同 vectorize_kb

    logger.info(
        "文档向量化完成 doc_id=%s chunks=%s collection=%s flushed=%s",
        doc.id, written, settings.MILVUS_COLLECTION, flushed,
    )
    return {"collection": settings.MILVUS_COLLECTION, "vectorized": written, "flushed": flushed}


def vectorize_kb(db: Session, user: User, kb_id: int) -> dict:
    """把一个知识库下**所有已处理完成**的文档一次性向量化。

    给"批量补数据"用：一份一份点太麻烦，而演示时需要库里先有数据才能检索。
    """
    knowledge_service.ensure_kb_permission(db, user, kb_id, knowledge_service.PERM_WRITE)

    docs = [
        d for d in document_repository.list_by_kb(db, kb_id)
        if d.status == STATUS_COMPLETED
    ]
    if not docs:
        raise AppException(ErrorCode.PARAM_ERROR, "该知识库下没有已处理完成的文档")

    vector_store.ensure_collection()
    written = 0
    for doc in docs:
        chunks = document_chunk_repository.list_by_document(db, doc.id)
        if not chunks:
            continue
        # 每份文档先清旧向量再写，理由同 vectorize_document
        vector_store.delete_by_document(doc.id)
        written += vector_store.upsert(_embed_chunks(chunks, kb_id))

    # 写完立刻落盘：不 flush 的话这些数据只活在内存段里，Milvus 一重启就对检索不可见
    # （2026-10-14 那次"提问查不到、重灌向量才好"的现象就是这么来的）。
    # 失败不推翻"已写入"这个事实，只是要如实标出来。一条都没写就不去惊动服务端。
    flushed = vector_store.flush() if written else True
    logger.info(
        "知识库向量化完成 kb_id=%s documents=%s chunks=%s flushed=%s",
        kb_id, len(docs), written, flushed,
    )
    return {"collection": settings.MILVUS_COLLECTION, "vectorized": written, "flushed": flushed}


# ==================== 检索 ====================


def _resolve_search_scope(
    db: Session, user: User, kb_ids: list[int] | None
) -> list[int] | None:
    """算出本次检索真正允许的范围。

    返回 None = 不受限（系统管理员）；返回 [] = 一个可检索的库都没有。

    **关键安全点**：客户端传来的 kb_ids 只是一个"缩小范围"的请求，
    必须和用户实际权限求交集 —— 绝不能直接拿去当过滤条件，
    否则前端传个别人的 kb_id 就能搜到不该看的内容。
    """
    allowed = knowledge_service.get_accessible_kb_ids(db, user)

    if allowed is None:
        # 系统管理员：不传就全量检索；传了就按传的收窄
        return sorted(set(kb_ids)) if kb_ids else None

    allowed_set = set(allowed)
    if kb_ids:
        return sorted(allowed_set & set(kb_ids))
    return sorted(allowed_set)


def _keyword_recall(
    db: Session, query: str, scope: list[int] | None, limit: int
) -> list[vector_store.SearchHit]:
    """关键词路召回：jieba 提词 → MySQL 字面匹配 → 转成与向量路**同构**的 hits。

    返回 `list[SearchHit]`（score = 命中词覆盖率，0~1）—— 让融合阶段能把两条路
    当成同一种东西处理（RRF 只关心名次，这个 score 只在"仅关键词命中"时用于展示）。

    提不出词（全是虚词）或没命中都返回 `[]`：关键词路空跑对融合是透明的，
    检索自动退化成纯向量 —— 这条路的失败模式必须是"没有贡献"，不是"拖垮检索"。
    """
    keywords = keyword.extract_keywords(query)
    if not keywords:
        return []
    rows = document_chunk_repository.search_by_keywords(db, keywords, scope, limit)
    if not rows:
        return []

    # 覆盖率 = 命中词数 / 提词总数。与余弦分同落 0~1，展示口径一致
    # （严格说是"词项覆盖率"而不是"相似度"—— 检索层已按 retrieval_path 标出来源，
    #  前端对"仅关键词"显示「关键词匹配」，不再混叫"相似度"）
    total = len(keywords)
    doc_ids = sorted({chunk.document_id for chunk, _ in rows})
    doc_map = {d.id: d for d in document_repository.get_by_ids(db, doc_ids)}

    hits: list[vector_store.SearchHit] = []
    for chunk, hit_count in rows:
        doc = doc_map.get(chunk.document_id)
        if doc is None:
            # 文档已被删而块还残留（外键未生效的历史数据）：与孤儿向量同性质，跳过
            continue
        hits.append(
            vector_store.SearchHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                knowledge_base_id=doc.knowledge_base_id,
                page_number=chunk.page_number,
                score=round(min(1.0, hit_count / total), 4),
            )
        )
    return hits


def _hydrate(db: Session, hits: list[vector_store.SearchHit]) -> tuple[list[dict], int]:
    """用 Milvus 命中的 chunk_id 回 MySQL 取正文和文件名。

    返回 `(结果列表, 被跳过的孤儿数)`。

    **为什么要返回孤儿数**：孤儿向量会白占 top_k 名额，让用户拿到的结果**变少**
    ——而这一点以前只体现在日志里。实测踩过一次：为了查"为什么只返回 1 条"
    把 limit、索引、一致性、加载状态全查了一遍，最后才发现是别的知识库的孤儿向量
    在占名额。有了这个计数，那次排查就变成看一个数字。

    Milvus 里刻意不存正文（大文本不适合放向量库），所以必须要回库取。批量取，避免 N+1。
    """
    if not hits:
        return [], 0

    chunk_ids = [h.chunk_id for h in hits]
    doc_ids = sorted({h.document_id for h in hits})
    chunk_map = {c.id: c for c in document_chunk_repository.get_by_ids(db, chunk_ids)}
    doc_map = {d.id: d for d in document_repository.get_by_ids(db, doc_ids)}

    results: list[dict] = []
    skipped = 0
    for hit in hits:
        chunk = chunk_map.get(hit.chunk_id)
        if chunk is None:
            # Milvus 里还有、MySQL 里已经没了 = 孤儿向量。
            # 正常不该出现（删文档会同步删向量），出现说明有一次清理没跑完，
            # 记一条日志、计数一个，但不要让它把整个检索搞崩。
            logger.warning("检索命中孤儿向量 chunk_id=%s（MySQL 中已不存在）", hit.chunk_id)
            skipped += 1
            continue
        doc = doc_map.get(hit.document_id)
        results.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": doc.filename if doc else "(文档已删除)",
                "knowledge_base_id": hit.knowledge_base_id,
                "page_number": chunk.page_number,
                "score": hit.score,
                "content": chunk.content,
            }
        )
    return results, skipped


# "没重排"的原因。排障时这三者的含义差别很大，所以显式给出来而不是只给一个 false：
#   全局开关关掉 / 只有一条候选（排序无意义）/ 重排服务失败已降级
RERANK_SKIP_DISABLED = "disabled"
RERANK_SKIP_NO_SCOPE = "no_scope"
RERANK_SKIP_SINGLE = "single_candidate"
RERANK_SKIP_FAILED = "failed"


def _apply_rerank(query: str, results: list[dict], top_k: int) -> tuple[list[dict], str | None]:
    """用 reranker 重排候选，返回 `(新顺序的结果, 未重排的原因)`。

    **失败必须降级，不能抛错**：rerank 是"锦上添花"，服务抖一下绝不能让整个问答答不出来。
    这与 embedding 的处理**相反** —— 那边拿不到查询向量就根本无法检索，所以必须失败。

    降级后仍是可用的检索结果（只是顺序回到向量分数），所以对用户表现为"答案略差"，
    而不是"功能坏了"。成功时原因返回 None。
    """
    try:
        pairs = reranker.rerank(query, [r["content"] for r in results], top_n=top_k)
    except AppException as exc:
        logger.warning("重排失败，降级为向量分数顺序：%s", exc.message)
        return results[:top_k], RERANK_SKIP_FAILED

    if not pairs:
        logger.warning("重排未返回结果，降级为向量分数顺序")
        return results[:top_k], RERANK_SKIP_FAILED

    out: list[dict] = []
    for index, score in pairs:
        item = dict(results[index])
        # 同时保留向量相似度：调试台/接口上能直接看出"重排到底改变了什么"
        item["vector_score"] = results[index]["score"]
        item["score"] = score
        out.append(item)
    return out, None


def search(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int = 5,
    rerank: bool = False,
) -> dict:
    """检索：算权限 → 双路召回（向量 + 关键词）→ RRF 融合 → 回库取正文 →（可选）重排。

    `rerank=True` 时走**两段式**：先多召回 `RAG_CANDIDATE_K` 条（保召回率），
    再由 reranker 精排到 top_k（保精度）。默认关 —— 所以 `/api/search` 默认不做重排，
    正好可以和自己对比"重排到底有没有用"；`/api/chat` 显式开启。

    `RAG_HYBRID_ENABLED`（默认开）时另走**关键词路**：jieba 提词做 MySQL 字面匹配，
    两路候选按名次 RRF 融合 —— 补纯向量对编号/专有名词不敏感的短板；
    关掉即退回纯向量（对照排查用）。关键词路空跑不拖累检索（融合对空路透明）。
    """
    scope = _resolve_search_scope(db, user, kb_ids)

    if scope == []:
        # 一个可访问的库都没有：直接返回空。
        # 这里绝对不能"省略 filter 去搜全库"——那就是越权。
        logger.info("检索范围为空，直接返回 query=%r", query[:50])
        return {
            "query": query, "top_k": top_k, "searched_kb_ids": [], "results": [],
            "reranked": False, "rerank_skipped_reason": RERANK_SKIP_NO_SCOPE,
            "candidate_k": 0, "skipped_orphans": 0,
        }

    use_rerank = bool(rerank) and settings.RAG_RERANK_ENABLED
    skip_reason = None if use_rerank else RERANK_SKIP_DISABLED
    candidate_k = max(top_k, settings.RAG_CANDIDATE_K) if use_rerank else top_k

    vector = embedding.embed_query(query)
    vector_hits = vector_store.search(vector, scope, candidate_k)

    # ---- 关键词路（混合检索）：与向量路并列的一条召回路径 ----
    # 关了开关或提不出词 → 空路；RRF 对空路透明（融合退化成向量路原顺序）。
    keyword_hits: list[vector_store.SearchHit] = []
    if settings.RAG_HYBRID_ENABLED:
        keyword_hits = _keyword_recall(db, query, scope, settings.RAG_KEYWORD_K)

    # RRF 融合两路名次，再统一截到候选预算 ——
    # 两路各召回 20 条，融合后**仍只 20 条进重排**（成本与纯向量一致），
    # 但"谁能进这 20 条"由两条路共同决定（关键词排前面的不再被向量路挤掉）。
    fused_order = rrf_merge(
        [[h.chunk_id for h in vector_hits], [h.chunk_id for h in keyword_hits]]
    )[:candidate_k]

    # 分数口径：向量路的命中保留**余弦相似度**（有"相似度"语义）；只有关键词路命中的
    # 用覆盖率分数（0~1，与余弦同量纲）。所以向量路优先，setdefault 保序不覆盖。
    hit_map: dict[int, vector_store.SearchHit] = {h.chunk_id: h for h in vector_hits}
    for h in keyword_hits:
        hit_map.setdefault(h.chunk_id, h)
    fused_hits = [hit_map[cid] for cid, _ in fused_order if cid in hit_map]

    results, skipped_orphans = _hydrate(db, fused_hits)

    # 给每条结果标**召回来源**（vector / keyword / both）—— 两条路的分数语义不同：
    # 仅关键词命中的 score 是词项覆盖率，不是余弦相似度；展示层据此把这类显示为
    # 「关键词匹配」而不是「相似度」。放在重排**之前**：_apply_rerank 是 dict 拷贝，
    # 标注会跟着走（它描述的是"哪条路召回了它"，与排序无关）。
    vector_ids = {h.chunk_id for h in vector_hits}
    keyword_ids = {h.chunk_id for h in keyword_hits}
    for r in results:
        in_vector = r["chunk_id"] in vector_ids
        in_keyword = r["chunk_id"] in keyword_ids
        r["retrieval_path"] = (
            "both" if in_vector and in_keyword else ("keyword" if in_keyword else "vector")
        )

    reranked = False
    if use_rerank:
        if len(results) > 1:
            results, skip_reason = _apply_rerank(query, results, top_k)
            reranked = skip_reason is None
        else:
            # 只有一条（或没有）就无所谓排序，不必白花一次重排调用
            skip_reason = RERANK_SKIP_SINGLE
    results = results[:top_k]

    # 顺手把**分数分布**打进日志：RAG 要不要相似度阈值（RAG_MIN_SCORE）现在定不了
    # —— 样本太少，设高了会漏掉本该命中的资料，比噪声更糟。先攒数据，
    # 攒够一批「问题 -> 命中分数」再回来看分布。一行日志，不建表、不落库。
    # hybrid= 后是两路各自的原始命中数（融合前）——"为什么结果变了"看这里。
    logger.info(
        "检索 user=%s scope=%s hybrid=%s 向量/关键词=%s/%s 候选=%s hits=%s reranked=%s "
        "跳过的孤儿=%s top_scores=%s",
        user.username, "ALL" if scope is None else scope,
        settings.RAG_HYBRID_ENABLED, len(vector_hits), len(keyword_hits),
        candidate_k, len(results), reranked, skipped_orphans,
        "/".join("%.3f" % r["score"] for r in results[:5]) or "-",
    )
    return {
        "query": query,
        "top_k": top_k,
        "searched_kb_ids": scope,
        "results": results,
        "reranked": reranked,
        # 没重排时给出原因（disabled / no_scope / single_candidate / failed）
        "rerank_skipped_reason": skip_reason,
        # 因为 Milvus 里残留孤儿而被跳过的条数：不为 0 说明检索结果被"吃掉"了几条
        "skipped_orphans": skipped_orphans,
        "candidate_k": candidate_k,
    }


# ==================== 自检与修复 ====================


def _orphan_kb_ids(db: Session) -> list[int]:
    """Milvus 里还有、MySQL 里已经没有的知识库 id。

    正常链路不该产生它们（删文档 / 删知识库都会同步清向量），
    出现就说明有一次清理没成功 —— 比如删的时候 Milvus 正好连不上（那条路径是吞异常的）。
    """
    known = {kb.id for kb in knowledge_repository.list_all(db)}
    return sorted(k for k in vector_store.list_kb_ids() if k not in known)


def self_check(db: Session) -> dict:
    """向量库自检。

    给管理员的运维接口用。**刻意不返回 Milvus 的地址** ——
    那是内网拓扑信息，要排查连接问题看服务端日志就够了（get_client 里已经打了）。

    🔴 每个读数**单独取、单独记失败原因**。旧实现把读失败压成 0/[]（`count()`
    吞异常返回 0），于是"读不到"和"真的是空的"在界面上完全一样 ——
    一次集合未加载就被报成"向量总数 0、3 篇文档全缺向量"，把人引去重灌数据，
    而数据其实一条都没少（2026-10-14 实测绕了这一圈才定位到真因）。
    现在这两件事是分开的：`read_errors` 非空 → 结论不可信；`visibility` 才是要看的那句人话。
    """
    try:
        info = vector_store.health()
    except AppException as exc:
        return {"available": False, "reason": exc.message}

    read_errors: dict[str, str] = {}

    def _read(name: str, fn, fallback):
        """取一个读数；失败就记下来并返回 fallback（None 表示"不知道"）。"""
        try:
            return fn()
        except AppException as exc:
            read_errors[name] = exc.message
            logger.warning("向量自检读数失败 %s err=%s", name, exc.message)
            return None

    visible = _read("vector_count", vector_store.count, None)
    # stored_rows 来自服务端元数据，**含已删除但尚未压缩的行**，所以它只用来判
    # "有没有数据"，绝不和 visible 比相等（实测见过 stored=27 / visible=10）。
    stored = _read("stored_rows", vector_store.stats_row_count, None)
    kb_ids = _read("kb_ids", vector_store.list_kb_ids, None)
    indexed_ids = _read("document_ids", vector_store.list_document_ids, None)

    orphans: list[int] | None = None
    orphan_count: int | None = None
    if kb_ids is not None:
        known = {kb.id for kb in knowledge_repository.list_all(db)}
        orphans = sorted(k for k in kb_ids if k not in known)
        orphan_count = sum(
            _read("orphan_count_%s" % k, lambda k=k: vector_store.count([k]), 0) or 0
            for k in orphans
        )

    missing = _documents_without_vectors(db, indexed_ids)
    expected = _expected_chunk_count(db)
    visibility, hint = _visibility_verdict(visible, stored, expected, read_errors)

    return {
        "available": True,
        "server_version": info["server_version"],
        "collection": info["collection"],
        "collection_exists": info["collection_exists"],
        "collections": info["collections"],
        "vector_count": visible,
        "kb_ids_in_vector_store": kb_ids,
        "orphan_kb_ids": orphans,
        "orphan_vector_count": orphan_count,
        # 与孤儿向量配对的反向问题：文档处理好了，但一条向量都没有 → 永远检索不到
        # （None = 读不到，不当成"全缺"）
        "documents_without_vectors": None if missing is None else len(missing),
        "documents_without_vectors_sample": (missing or [])[:10],
        # ---- 新增：把"数据在不在"和"读不读得到"分开说清楚 ----
        "stored_rows": stored,
        "expected_chunks": expected,
        "visibility": visibility,
        "visibility_hint": hint,
        "read_errors": read_errors or None,
        # 同一个 Milvus 里结构不属于本应用的集合（永远不会被检索用到，但容易看成"有数据"）
        "foreign_collections": _read("foreign_collections", vector_store.foreign_collections, None),
    }


def _expected_chunk_count(db: Session) -> int:
    """MySQL 侧"应该有的向量条数" = 所有已完成文档的切块数之和。

    用 `documents.chunk_count` 而不是现算 chunk 表：这个字段在文档处理里
    与切块同事务写入，而自检不该为了一个指标去全表 COUNT。
    """
    return sum(
        d.chunk_count or 0
        for d in document_repository.list_all(db)
        if d.status == STATUS_COMPLETED
    )


def _visibility_verdict(
    visible: int | None, stored: int | None, expected: int, errors: dict[str, str]
) -> tuple[str, str]:
    """把三个读数合成一句结论。返回 `(visibility, visibility_hint)`。

    visibility ∈ {ok, partial, missing, not_visible, empty, unknown}
    """
    if errors:
        return "unknown", "有指标读取失败（%s），下面的数字不可信 —— 先确认 Milvus 是否可用" % (
            "、".join(errors)
        )
    if visible == 0 and expected > 0:
        if stored:
            # 这一条就是 2026-10-14 那个现象的名字：数据在库里，检索就是读不到
            return "not_visible", (
                "向量在（服务端记着 %s 行），但检索一条都读不到 —— 集合未加载或未落盘。"
                "重新向量化能临时恢复，根治要在 Milvus 侧 flush + load" % stored
            )
        return "missing", (
            "%s 个切块还没有对应向量，这些文档检索不到 —— 去「文档管理」点批量向量化" % expected
        )
    if visible == 0 and expected == 0:
        return "empty", "还没有已处理完成的文档被向量化（这是正常起点）"
    if visible < expected:
        return "partial", (
            "检索可见 %s 条，按切块数应有 %s 条 —— 差额通常是文档重跑后留下的旧块" % (visible, expected)
        )
    return "ok", "向量条数与切块数一致，检索可读"


def _documents_without_vectors(db: Session, indexed_ids: list[int] | None) -> list[dict] | None:
    """已处理完成、却一条向量都没有的文档。

    这是孤儿向量的**镜像问题**：
      · 孤儿向量 = 向量多出来了 → 白占 top_k 名额，让用户的检索结果变少
      · 向量缺失 = 向量少了     → 文档**根本检索不到**，答案里永远引用不到它
    两种都会静默改变检索结果，而 `documents` 表里没有任何向量相关字段，
    从文档列表和接口上都看不出来 —— 所以放进维护接口，让管理员能查。

    `indexed_ids` 由调用方读一次传进来（**不在这里读**）：读不到时必须报"不知道"
    而不是把全部文档算成缺 —— 那正是旧实现误导人的地方。返回 None 表示无法判断。

    代价说清楚：它要把 Milvus 里出现过的 document_id 都取回来（去重），
    数据量大时开销高，因此**只挂在维护接口上，不进请求链路**。
    """
    if indexed_ids is None:
        return None
    indexed = set(indexed_ids)
    missing: list[dict] = []
    for doc in document_repository.list_all(db):
        if doc.status == STATUS_COMPLETED and doc.id not in indexed:
            missing.append(
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "knowledge_base_id": doc.knowledge_base_id,
                }
            )
    return missing


def purge_orphans(db: Session) -> dict:
    """清掉孤儿向量（所属知识库已经不存在的那些）。

    只删"MySQL 里查不到这个 kb_id"的向量，不会碰任何仍然有效的知识库。
    """
    orphans = _orphan_kb_ids(db)
    removed = 0
    for kb_id in orphans:
        removed += vector_store.delete_by_kb(kb_id)
    # 一次删除只 flush 一次（循环里逐条 flush 会把一个运维操作放大成 N 次服务端等待）
    flushed = vector_store.flush() if removed else True
    logger.info("孤儿向量清理 kb_ids=%s removed=%s flushed=%s", orphans, removed, flushed)
    return {"orphan_kb_ids": orphans, "removed": removed, "flushed": flushed}
