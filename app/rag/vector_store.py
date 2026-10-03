"""Milvus 向量库封装。

存什么、不存什么（这是本模块最重要的设计决定）：
  **Milvus 只存"向量 + 定位信息"**：chunk_id / document_id / knowledge_base_id / page_number
  **正文不存**：大段文本不适合放向量库，它擅长的是相似度检索不是文本存储。
  检索命中后拿 chunk_id 回 MySQL 取正文（见 vector_service._hydrate）。

collection 名字与 MySQL 表 document_chunks 同名 —— 两边存的是同一种东西（切分块），
名字一致，看代码时对应关系一眼看得出。

权限过滤在这里落地：
  search() 的 kb_ids 参数会变成 Milvus 的 filter 表达式 `knowledge_base_id in [...]`。
  **过滤发生在检索阶段**，不是"先全量检索再判权限"。
"""

import logging
from dataclasses import dataclass

from pymilvus import DataType, MilvusClient

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

_client: MilvusClient | None = None

# 字段名常量：拼错字段名 Milvus 只会给一个含糊的报错，用常量省事
F_ID = "chunk_id"
F_DOC = "document_id"
F_KB = "knowledge_base_id"
F_PAGE = "page_number"
F_VECTOR = "vector"

# 余弦相似度。BGE 系列向量取余弦是标准做法（只关心方向、不关心长度）。
METRIC_TYPE = "COSINE"

# 一致性级别：默认是 Bounded（最终一致）——**刚写入的向量立刻查不到**，
# 实测就是这个现象：向量化写进去 2 条，紧接着 count 只看到 1 条、检索也只命中 1 条。
# 本项目数据量很小、单机 Milvus，"写完立刻能搜到"对使用体验是刚需
# （用户点完向量化马上检索，搜不到会以为坏了），所以用 Strong。
# 代价是每次检索要等一次时间戳同步，在这个数据规模下可以忽略。
# 将来数据量真上来了，这里应该改回 Bounded 并让业务接受短暂延迟。
CONSISTENCY_LEVEL = "Strong"


@dataclass(frozen=True)
class ChunkVector:
    """一条待写入的向量记录。"""

    chunk_id: int            # 同时是 Milvus 的主键（用 MySQL 的 chunk id，天然唯一）
    document_id: int
    knowledge_base_id: int
    page_number: int | None
    vector: list[float]


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    document_id: int
    knowledge_base_id: int
    page_number: int | None
    score: float


# ==================== 连接 ====================


def get_client() -> MilvusClient:
    """惰性单例。连不上就抛 50004（MILVUS_ERROR）。

    错误 message **不含主机端口**：那是内网拓扑信息，不该返给客户端
    （项目既有口径见 api/health.py —— 真因进日志，响应只给一句话）。
    排查时看服务端日志，下面的 logger 已经把 uri 打出来了。
    """
    global _client
    if _client is None:
        uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
        try:
            _client = MilvusClient(uri=uri, timeout=settings.MILVUS_TIMEOUT)
            version = _client.get_server_version()
            logger.info("Milvus 已连接 uri=%s server_version=%s", uri, version)
        except Exception as exc:  # noqa: BLE001
            _client = None
            logger.warning("Milvus 连接失败 uri=%s err=%s", uri, exc)
            raise AppException(
                ErrorCode.MILVUS_ERROR, "向量服务暂时不可用，请稍后重试"
            )
    return _client


def health() -> dict:
    """自检用：服务端版本 + collection 列表 + 本 collection 是否存在。

    **不返回 Milvus 地址** —— 这是给 HTTP 自检接口复用的，
    地址属于内网信息，日志里有就够了。
    """
    client = get_client()
    collections = list(client.list_collections())
    return {
        "server_version": str(client.get_server_version()),
        "collection": settings.MILVUS_COLLECTION,
        "collection_exists": settings.MILVUS_COLLECTION in collections,
        "collections": sorted(collections),
    }


# ==================== 建表 ====================


def ensure_collection() -> None:
    """建 collection + 索引并 load。**幂等**：已存在就直接返回。"""
    client = get_client()
    name = settings.MILVUS_COLLECTION

    if client.has_collection(name):
        return

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(F_ID, DataType.INT64, is_primary=True)
    schema.add_field(F_DOC, DataType.INT64)
    schema.add_field(F_KB, DataType.INT64)
    schema.add_field(F_PAGE, DataType.INT64, nullable=True)
    schema.add_field(F_VECTOR, DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name=F_VECTOR, index_type="AUTOINDEX", metric_type=METRIC_TYPE
    )
    # 给 knowledge_base_id 建标量索引：每次检索都要按它过滤，
    # 没有索引就得逐行比，数据量上来后差距很明显。
    # INVERTED 在部分 Milvus 版本上不可用，失败不影响检索（只是慢），所以降级处理。
    try:
        index_params.add_index(field_name=F_KB, index_type="INVERTED")
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge_base_id 标量索引创建失败，检索将退化为逐行过滤: %s", exc)

    client.create_collection(
        collection_name=name, schema=schema, index_params=index_params
    )
    logger.info(
        "已创建 Milvus collection=%s dim=%s metric=%s",
        name, settings.EMBEDDING_DIM, METRIC_TYPE,
    )


# ==================== 写 ====================


def upsert(rows: list[ChunkVector]) -> int:
    """写入或覆盖。

    主键是 chunk_id：同一份文档重复向量化不会产生重复行（旧行会被覆盖）。
    但**主键变了就不会**（比如文档重新处理过，chunk_id 是一批新的）——
    那批旧向量得靠调用方先 delete_by_document 清掉，见 vector_service.vectorize_document。
    """
    if not rows:
        return 0
    client = get_client()
    data = [
        {
            F_ID: row.chunk_id,
            F_DOC: row.document_id,
            F_KB: row.knowledge_base_id,
            # 纯文本类文档（txt/md）没有"页"的概念，page_number 就是 None。
            # Milvus 侧该字段建的是 nullable=True，可以直接存 null，
            # 不需要拿 0 当哨兵值（用 0 反而会和"第 0 页"混淆）。
            F_PAGE: row.page_number,
            F_VECTOR: row.vector,
        }
        for row in rows
    ]
    try:
        client.upsert(collection_name=settings.MILVUS_COLLECTION, data=data)
    except Exception as exc:  # noqa: BLE001
        # 上游原文只进日志：它可能带 collection 名、filter 表达式、服务端地址
        logger.warning("写入 Milvus 失败 rows=%s err=%s", len(rows), exc)
        raise AppException(ErrorCode.MILVUS_ERROR, "向量写入失败，请稍后重试") from exc
    return len(data)


def delete_by_document(document_id: int) -> int:
    """删掉某文档的全部向量。返回受影响的条数。

    注意：Milvus 的 delete 返回的是"删除操作的统计"，不同版本给出的
    delete_count 口径不完全一致，所以这里只做日志用途，不当作强保证。
    """
    client = get_client()
    try:
        result = client.delete(
            collection_name=settings.MILVUS_COLLECTION, filter=f"{F_DOC} == {int(document_id)}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除文档向量失败 doc_id=%s err=%s", document_id, exc)
        raise
    return int((result or {}).get("delete_count", 0))


def delete_by_kb(knowledge_base_id: int) -> int:
    client = get_client()
    try:
        result = client.delete(
            collection_name=settings.MILVUS_COLLECTION,
            filter=f"{F_KB} == {int(knowledge_base_id)}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除知识库向量失败 kb_id=%s err=%s", knowledge_base_id, exc)
        raise
    return int((result or {}).get("delete_count", 0))


def count(kb_ids: list[int] | None = None) -> int:
    """统计条数。kb_ids 为 None 表示全部；空列表返回 0（不是"全部"）。"""
    if kb_ids is not None and not kb_ids:
        return 0
    expr = build_kb_filter(kb_ids)
    client = get_client()
    try:
        rows = client.query(
            collection_name=settings.MILVUS_COLLECTION,
            filter=expr or "",
            output_fields=["count(*)"],
            consistency_level=CONSISTENCY_LEVEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("统计 Milvus 条数失败 err=%s", exc)
        return 0
    if not rows:
        return 0
    return int(list(rows[0].values())[0])


def list_kb_ids() -> list[int]:
    """列出 collection 里出现过的所有 knowledge_base_id（去重）。

    **只给维护/自检用**，不要放进请求链路：它要把行都取回来才能去重，
    数据量大时开销很高。用途是找出"MySQL 里已经没有、Milvus 里还留着"的孤儿向量。
    """
    client = get_client()
    try:
        rows = client.query(
            collection_name=settings.MILVUS_COLLECTION,
            filter="",
            output_fields=[F_KB],
            consistency_level=CONSISTENCY_LEVEL,
            limit=16384,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 knowledge_base_id 列表失败 err=%s", exc)
        return []
    return sorted({int(r[F_KB]) for r in rows if r.get(F_KB) is not None})


# ==================== 检索 ====================


def build_kb_filter(kb_ids: list[int] | None) -> str | None:
    """把允许的知识库 id 列表拼成 Milvus 的 filter 表达式。

    - None  → None，表示不加过滤（仅系统管理员路径使用）
    - []    → 调用方必须**提前返回空结果**，不要拿它来检索：
              没有可过滤条件时若退化成"不过滤"，就等于搜全库，属于越权。
    - [2,1] → "knowledge_base_id in [1, 2]"

    提取成纯函数是为了能单独测：这是权限相关的一行代码，值得有测试盯着。

    ⚠️ 已知规模上限：非管理员不传 kb_ids 时，范围 = 全部可访问库，
    而 is_public 的设计天然让普通员工的 scope 偏大。库数上百时这个 IN 表达式会很长，
    过滤开销随之上升。当前不是问题（库数是个位数），但这是"检索期过滤"方案的固有代价；
    真到那一天，替代做法是**按知识库分区（partition）**，或反转成"排除列表"。
    """
    if kb_ids is None:
        return None
    return f"{F_KB} in [{', '.join(str(int(i)) for i in sorted(set(kb_ids)))}]"


def _hit_primary_key(item: dict) -> int | None:
    """从一条命中里取主键。

    实测（pymilvus 3.0.2）：主键放在**字段名**下面，也就是 `item["chunk_id"]`；
    而 2.x 版本用的是固定的 `item["id"]`。两种都认，免得换个客户端版本就崩。
    """
    for key in (F_ID, "id"):
        value = item.get(key)
        if value is not None:
            return int(value)
    return None


def search(vector: list[float], kb_ids: list[int] | None, top_k: int) -> list[SearchHit]:
    """向量检索。

    kb_ids：允许检索的知识库范围（由 vector_service 从用户权限算出）。
    """
    if kb_ids is not None and not kb_ids:
        # 防御性检查：调用方本该拦在前面。这里再挡一次，
        # 因为"空范围被当成不过滤"是会造成越权的错误，代价太高。
        logger.warning("search() 收到空 kb_ids，已直接返回空结果（避免退化为全库检索）")
        return []

    client = get_client()
    try:
        results = client.search(
            collection_name=settings.MILVUS_COLLECTION,
            data=[vector],
            limit=top_k,
            filter=build_kb_filter(kb_ids) or "",
            output_fields=[F_DOC, F_KB, F_PAGE],
            search_params={"metric_type": METRIC_TYPE},
            consistency_level=CONSISTENCY_LEVEL,
        )
    except Exception as exc:  # noqa: BLE001
        # 上游原文只进日志（可能含 filter 表达式、集合名等内部细节）
        logger.warning("Milvus 检索失败 kb_ids=%s err=%s", kb_ids, exc)
        raise AppException(ErrorCode.MILVUS_ERROR, "向量检索失败，请稍后重试") from exc

    hits: list[SearchHit] = []
    for group in results:
        for item in group:
            chunk_id = _hit_primary_key(item)
            if chunk_id is None:
                logger.warning("检索命中缺少主键，已跳过: %s", str(item)[:200])
                continue
            entity = item.get("entity") or {}
            page = entity.get(F_PAGE)
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    document_id=int(entity.get(F_DOC, 0)),
                    knowledge_base_id=int(entity.get(F_KB, 0)),
                    # page_number 在 Milvus 里是 nullable，txt/md 存的就是 null；
                    # 这里原样透传 None，不做 0/None 的来回换算。
                    page_number=int(page) if page is not None else None,
                    score=float(item.get("distance", 0.0)),
                )
            )
    return hits
