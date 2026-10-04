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
from app.rag import embedding, reranker, vector_store
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

    logger.info(
        "文档向量化完成 doc_id=%s chunks=%s collection=%s",
        doc.id, written, settings.MILVUS_COLLECTION,
    )
    return {"collection": settings.MILVUS_COLLECTION, "vectorized": written}


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

    logger.info(
        "知识库向量化完成 kb_id=%s documents=%s chunks=%s",
        kb_id, len(docs), written,
    )
    return {"collection": settings.MILVUS_COLLECTION, "vectorized": written}


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
    """向量检索：算权限 → 向量化查询 → 带 filter 检索 → 回库取正文 →（可选）重排。

    `rerank=True` 时走**两段式**：先多召回 `RAG_CANDIDATE_K` 条（保召回率），
    再由 reranker 精排到 top_k（保精度）。默认关 —— 所以 `/api/search` 仍是纯向量检索，
    正好可以和自己对比"重排到底有没有用"；`/api/chat` 显式开启。
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
    hits = vector_store.search(vector, scope, candidate_k)
    results, skipped_orphans = _hydrate(db, hits)

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
    logger.info(
        "向量检索 user=%s scope=%s 候选=%s hits=%s reranked=%s 跳过的孤儿=%s top_scores=%s",
        user.username, "ALL" if scope is None else scope, candidate_k, len(results),
        reranked, skipped_orphans,
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
    """
    try:
        info = vector_store.health()
    except AppException as exc:
        return {"available": False, "reason": exc.message}

    orphans = _orphan_kb_ids(db)
    missing = _documents_without_vectors(db)
    return {
        "available": True,
        "server_version": info["server_version"],
        "collection": info["collection"],
        "collection_exists": info["collection_exists"],
        "collections": info["collections"],
        "vector_count": vector_store.count(),
        "kb_ids_in_vector_store": vector_store.list_kb_ids(),
        "orphan_kb_ids": orphans,
        "orphan_vector_count": sum(vector_store.count([k]) for k in orphans),
        # 与孤儿向量配对的反向问题：文档处理好了，但一条向量都没有 → 永远检索不到
        "documents_without_vectors": len(missing),
        "documents_without_vectors_sample": missing[:10],
    }


def _documents_without_vectors(db: Session) -> list[dict]:
    """已处理完成、却一条向量都没有的文档。

    这是孤儿向量的**镜像问题**：
      · 孤儿向量 = 向量多出来了 → 白占 top_k 名额，让用户的检索结果变少
      · 向量缺失 = 向量少了     → 文档**根本检索不到**，答案里永远引用不到它
    两种都会静默改变检索结果，而 `documents` 表里没有任何向量相关字段，
    从文档列表和接口上都看不出来 —— 所以放进维护接口，让管理员能查。

    代价说清楚：它要把 Milvus 里出现过的 document_id 都取回来（去重），
    数据量大时开销高，因此**只挂在维护接口上，不进请求链路**。
    """
    indexed = set(vector_store.list_document_ids())
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
    logger.info("孤儿向量清理 kb_ids=%s removed=%s", orphans, removed)
    return {"orphan_kb_ids": orphans, "removed": removed}
