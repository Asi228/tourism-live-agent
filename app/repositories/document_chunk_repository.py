"""文档切分块数据访问。"""

from sqlalchemy import case, delete as sa_delete, or_, select
from sqlalchemy.orm import Session

from app.models.document import STATUS_COMPLETED, Document
from app.models.document_chunk import DocumentChunk


def get_by_id(db: Session, chunk_id: int) -> DocumentChunk | None:
    """按主键取一个切分块（**引用溯源**用）。

    ⚠️ 同 `message_repository.get_by_id`：这里**不做归属判断** ——
    块的权限 = 文档的权限 = 文档所属知识库的权限，该由 service 层判定。
    """
    return db.get(DocumentChunk, chunk_id)


def list_by_document(db: Session, document_id: int) -> list[DocumentChunk]:
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(db.scalars(stmt))


def get_by_ids(db: Session, chunk_ids: list[int]) -> list[DocumentChunk]:
    """按 id 列表批量取回。

    向量检索命中一批 chunk_id 后要用它一次性取回正文 ——
    逐个 db.get 就是典型 N+1（top_k=5 就是 5 条 SQL）。
    """
    if not chunk_ids:
        return []
    stmt = select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
    return list(db.scalars(stmt))


def search_by_keywords(
    db: Session,
    keywords: list[str],
    kb_ids: list[int] | None,
    limit: int,
) -> list[tuple[DocumentChunk, int]]:
    """关键词路召回，按**命中词数**降序。返回 `[(chunk, 命中词数)]`。

    三个口径与检索层严格对齐（不对齐就会出现"检索搜不到、这条能搜到"的偏差）：
      - **只认 completed 文档**：处理中/失败的文档命中的是上一轮旧切块；
      - `kb_ids` 由调用方与用户权限**求过交集**后才传进来（None = 管理员不受限），
        本函数不做权限判断 —— 与 `vector_store.search` 的 filter 同一个约定；
      - LIKE 分支用 `contains(autoescape=True)`：用户输入里的 `%` `_` 必须转义，
        否则会变成通配符（`%` 能把半库捞出来）。

    **两套实现按方言分派**（2026-10-15）：
      - MySQL → `MATCH ... AGAINST` 走 FULLTEXT ngram 索引召回（块数多了不扫全表）；
      - 其它（SQLite 测试库）→ 原 LIKE 字面匹配。
    排序口径两套**逐字一致**（命中词数 desc, id），保证同一查询在测试与线上
    行为对得上；召回语义也一致（**完整词命中**才进候选，见 MySQL 分支的过滤说明）。
    """
    if not keywords:
        return []
    if db.bind.dialect.name == "mysql":
        return _search_by_keywords_mysql(db, keywords, kb_ids, limit)
    return _search_by_keywords_like(db, keywords, kb_ids, limit)


def _search_by_keywords_like(
    db: Session,
    keywords: list[str],
    kb_ids: list[int] | None,
    limit: int,
) -> list[tuple[DocumentChunk, int]]:
    """字面匹配实现（SQLite / 测试环境；MySQL 的兜底分支）。

    数据量小（企业内网制度库量级）时精度足够、部署零成本。
    """
    hit_expr = None
    for kw in keywords:
        term = case((DocumentChunk.content.contains(kw, autoescape=True), 1), else_=0)
        hit_expr = term if hit_expr is None else hit_expr + term

    stmt = (
        select(DocumentChunk, hit_expr.label("hit_count"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(or_(*[DocumentChunk.content.contains(kw, autoescape=True) for kw in keywords]))
        .where(Document.status == STATUS_COMPLETED)
        # 排序次键用 id：命中词数相同时保证确定顺序（否则每次查询结果顺序可能抖动）
        .order_by(hit_expr.desc(), DocumentChunk.id)
        .limit(limit)
    )
    if kb_ids is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(kb_ids))

    rows = db.execute(stmt).all()
    return [(row[0], int(row[1] or 0)) for row in rows]


def _search_by_keywords_mysql(
    db: Session,
    keywords: list[str],
    kb_ids: list[int] | None,
    limit: int,
) -> list[tuple[DocumentChunk, int]]:
    """FULLTEXT ngram 实现（MySQL；索引见迁移 d9e4f1a2b3c5）。

    为什么召回走 FULLTEXT、排序不走它的相关度：排序口径必须与 LIKE 分支
    **逐字一致**（命中词数 desc, id），否则同一查询在两套环境里结果顺序不同，
    测试与线上行为对不上。所以这里用 `INSTR(...) > 0` 逐词求和当命中词数
    （MySQL 里布尔表达式当 1/0 用，每个词自带括号防止运算优先级把式子咬乱）。

    🔴 **为什么返回前必须过滤 `hit_count > 0`**（2026-10-15 评审实锤 + 真库复现）：
    ngram_token_size=2 会把查询切成二元组（`ISO27001` → `IS/SO/O2/27/70/01`），
    NATURAL LANGUAGE MODE 下**任一二元组命中即召回** —— 于是"一个字都不含完整词、
    只共享某个 2-gram"的块也会被召回（实测：库里没有 ISO27001 完整串时，
    该查询召回了 9 行、其中 8 行 hit_count=0）。这些 0 分行会按 id 兜底排进
    关键词候选、参与 RRF，把向量路的真命中挤出 candidate_k —— 正好打在
    "关键词路补编号/专名词短板"的立项目标上。过滤之后两分支召回语义才真正一致
    （LIKE 的 OR 条件天然保证 hit_count≥1；FULLTEXT 不保证，所以显式过滤）。
    代价为零：INSTR 已经在算，只是把 0 分行丢掉。

    ⚠️ ngram parser 默认 token 2 字 —— 与提词侧"单字全滤"的口径天然对齐；
    真出现单字词也到不了这里（keyword.py 提前滤掉了）。
    """
    from sqlalchemy import text as sa_text

    params: dict = {"terms": " ".join(keywords)}
    for i, kw in enumerate(keywords):
        params[f"kw_{i}"] = kw
    # 命中词数 = 逐词 INSTR 求和（MySQL 里布尔表达式当 1/0 用）。
    # 每个词自带括号，防止运算优先级把式子咬乱。
    # TextClause 不支持 + / .label() / .desc()，所以整条写成一段：
    # SELECT 里起别名 hit_count，ORDER BY 引用该别名（MySQL 允许）——
    # 这样排序表达式不会二次展开 bindparam。
    # 代价：每个候选行要做"词数次"TEXT 子串扫描。当前 limit=20 无所谓，
    # 将来候选预算调大时要重新掂量（NN 是候选集行数、K 是词数）。
    hit_sql = " + ".join(
        f"(INSTR(document_chunks.content, :kw_{i}) > 0)" for i in range(len(keywords))
    )
    hit_col = sa_text(f"({hit_sql}) AS hit_count")
    match_clause = sa_text(
        "MATCH(document_chunks.content) AGAINST (:terms IN NATURAL LANGUAGE MODE)"
    )

    stmt = (
        select(DocumentChunk).add_columns(hit_col)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(match_clause)
        .where(Document.status == STATUS_COMPLETED)
        .order_by(sa_text("hit_count DESC"), DocumentChunk.id)
        .limit(limit)
    )
    if kb_ids is not None:
        stmt = stmt.where(Document.knowledge_base_id.in_(kb_ids))

    # 参数走执行时传入（`db.execute(stmt, params)`）：挂在 Select.params() 上
    # 在 ORM execute 路径上不会带下去（实测 [parameters: [{}]]）。
    rows = db.execute(stmt, params).all()
    # 🔴 过滤 0 分行：ngram 的 2-gram 召回会带进"不含完整词"的块，
    # 详见函数 docstring 的实锤说明。不过滤 = 关键词路往融合里注噪声，
    # 把向量路真命中挤出候选预算。
    return [(row[0], int(row[1] or 0)) for row in rows if int(row[1] or 0) > 0]


def bulk_create(
    db: Session,
    document_id: int,
    items: list[tuple[int, int | None, str]],
) -> int:
    """批量插入 chunk。items = [(chunk_index, page_number, content), ...]

    用 add_all + flush 而不是逐条 add：一份文档几百块，
    逐条 add 会触发几百次 flush 检查，明显更慢。
    """
    if not items:
        return 0
    db.add_all(
        [
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                page_number=page_number,
                content=content,
                char_count=len(content),
            )
            for index, page_number, content in items
        ]
    )
    db.flush()
    return len(items)


def delete_by_document(db: Session, document_id: int) -> int:
    """删除某文档的全部 chunk。

    重新处理前先清空，让"处理"变成幂等操作 ——
    否则重跑一次就会撞上 uq_doc_chunk_index 唯一约束。
    """
    result = db.execute(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
    db.flush()
    return result.rowcount or 0
