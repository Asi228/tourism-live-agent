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

关于导入方式 —— **别把它提到文件顶部**（这不是随手写的风格）：

  本模块顶层 `import pymilvus`，而它是个重包：实测**首次导入约 1.1 秒**
  （pymilvus 带 grpc 等一串依赖），且导入失败时会直接抛异常。
  所以**所有使用方都写成函数内局部 import，并且一律包在 `try` 里**：

      try:
          from app.rag import vector_store
          vector_store.xxx()
      except Exception:
          ...          # Milvus 不可用只影响这一步

  这样两件事同时成立：
    · 不碰 Milvus 的路径（纯文档解析、MySQL 探活等）**不付那 1.1 秒**；
    · **Milvus 环境坏掉不会让整个应用起不来** —— 影响面被限制在调用点那一个
      `try` 里：删库时清不掉向量、概览里向量数显示 "-"、探活报 fail，
      而其它功能照常。

  提到顶层会让上面两条一起失效（导入失败就是**整个 app 启动失败**）。
  调用点：`document_service` / `knowledge_service` / `stats_service` / `api/health`。
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


def probe(timeout_seconds: float) -> None:
    """健康检查专用的**短超时**探活。通不过就抛异常。

    为什么不能直接复用 `get_client()`（这里踩过一个很难看的坑）：
      · 共享单例的超时是 `MILVUS_TIMEOUT`（默认 **10 秒**）；
      · 而且它**失败时不缓存**（except 里把 `_client` 置回 None），所以每次调用都会重试。
      两件事叠加的结果是：VM 一关机，**每一次探活都要等满 10 秒**。
      探活要回答的是"通不通"，不是"等到服务端回话" —— 10 秒的探测会让探活脚本先超时，
      于是"Milvus 挂了"被误报成"/health 无响应"，等于把故障换了个说法。

    所以这里**另建一个一次性客户端**、只给它这么点超时，用完即弃：
      · 不碰共享单例（绝不能为了探活去调低全局 MILVUS_TIMEOUT，那是检索路径的超时）；
      · 调用方（api/health.py）有结果缓存，所以"每次新建客户端"的开销可以接受。
    """
    uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    client = MilvusClient(uri=uri, timeout=timeout_seconds)
    client.get_server_version()


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


def _load_into_querynode(client, name: str) -> bool:
    """把集合加载进检索节点。**幂等**，失败只记日志并返回 False。

    🔴 为什么"集合已存在"还不够（2026-10-14 实测定位到的现象）：
    Milvus 2.6.15 上出现过 `get_load_state()=Loaded` 但检索/统计一律返回空的状况 ——
    那 10 条向量用**主键点查**查得到，`search()` 与 `count(*)` 却一条都读不到，
    用户侧的表现就是"文档明明向量化过，提问却说没找到；重新向量化一次立刻正常"。
    （重新向量化能"治好"它，是因为那次写入顺带把数据变成了可读状态。）
    `load_collection` 是幂等的，所以在"集合已存在"这条分支上也补一次，
    让应用有机会自愈，而不是每次都要人肉去点一遍向量化。

    这里**吞异常**是刻意的：调用方是"尽力把系统弄成可用"的语义，
    加载失败不该把一次本来能成功的向量化整个打挂。
    """
    try:
        if not client.has_collection(name):
            return False
        client.load_collection(collection_name=name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载 Milvus collection=%s 失败（不阻断，检索可能读不到数据）: %s", name, exc)
        return False


def ensure_loaded() -> bool:
    """公开入口：连上客户端再加载一次。**测试里会被哑元替掉**（见 conftest 的写操作防护）。"""
    return _load_into_querynode(get_client(), settings.MILVUS_COLLECTION)


def _flush_best_effort(client_provider, name: str) -> bool:
    """落盘的执行体：**任何异常都吞掉**，只返回 True/False。

    连不上时 `client_provider()`（也就是 `get_client`）自己会抛，所以它一并放进 try：
    调用方拿到的只会是布尔，不会出现"数据已经写成功、却因为落盘这一步回不了头
    而整个请求失败"那种最难看的失败。
    （拆成"执行体 + 薄壳"是为了能被用例直接调用：`flush` 这个名字在测试里会被
    conftest 的写操作哑元替掉，直接测 `flush()` 测的是哑元。）
    """
    try:
        client_provider().flush(name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Milvus flush 失败 collection=%s err=%s", name, str(exc)[:200])
        return False


def flush() -> bool:
    """把内存段落盘。**不抛异常**，失败返回 False，由调用方决定怎么向用户表述。

    为什么必须显式 flush：不 flush 的话，写入只活在 growing segment 里，
    服务端重启后那些数据对检索路径就是**不可见**的 —— 上面那条现象的根源。
    写完就 flush，"向量化完成"这句话才在重启之后依然成立。

    实测对照（2026-10-14，同一个 collection）：flush 前 `list_persistent_segments`
    全是 `num_rows=0` 的段（一条数据都没落盘），flush 之后立刻出现
    `rows=37, state=Flushed` 并且进入已加载段 —— 所以这不是"以防万一"，是补上缺的那一步。
    """
    return _flush_best_effort(get_client, settings.MILVUS_COLLECTION)


def foreign_collections() -> list[str]:
    """列出**结构不是本应用的**那些集合（同一个 Milvus 里的其它库）。

    判据用主键字段名：本应用的 collection 主键是 `chunk_id`。
    为什么要专门报它：实测同一个 Milvus 里躺着一个 10 行的 `docs` 集合（字段只有
    `id` + `vector`），排查时"看到有向量"就会误判数据在，而本应用根本读不到它 ——
    这次绕的弯路有一半是它造成的。它不该被本应用删（不属于我们的东西），但必须被指出来。

    单个集合 describe 失败就跳过：这是自检的附加信息，不该因为它把整个自检搞挂。
    """
    client = get_client()
    ours = settings.MILVUS_COLLECTION
    foreign: list[str] = []
    for name in client.list_collections():
        if name == ours:
            continue
        try:
            fields = {f.get("name") for f in client.describe_collection(name).get("fields", [])}
        except Exception as exc:  # noqa: BLE001
            logger.info("describe collection=%s 失败，跳过陌生集合判断 err=%s", name, str(exc)[:120])
            continue
        if F_ID not in fields:
            foreign.append(name)
    return sorted(foreign)


def ensure_collection() -> None:
    """建 collection + 索引并 load。**幂等**：已存在也要确保它处于可检索状态。"""
    client = get_client()
    name = settings.MILVUS_COLLECTION

    if client.has_collection(name):
        # 早返回是旧实现的问题所在：存在 ≠ 可检索（见 _load_into_querynode 的实测记录）
        _load_into_querynode(client, name)
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
    # 新建之后同样要确保 load（create_collection 在不同版本上是否自动加载并不一致，
    # 而这里不依赖那个差异：显式调一次，幂等且便宜）
    _load_into_querynode(client, name)


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
    """统计条数。kb_ids 为 None 表示全部；空列表返回 0（不是"全部"）。

    🔴 读失败**抛异常**，不再返回 0。旧实现 `except: return 0` 把"读不到"和
    "真的是空的"压成了同一个值，后果是自检会在数据明明存在时报"向量总数 0 /
    缺向量文档 N 篇"（2026-10-14 那次排查就被这个假警报带偏过一轮 ——
    当时向量一条都没少，只是检索路径读不到）。
    调用方要区分这两种情况：`stats_service` 已经会 catch 并把该项显示成"不可用"，
    `vector_service.self_check()` 会把它记进 `read_errors`。
    """
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
        raise AppException(ErrorCode.MILVUS_ERROR, "向量条数统计失败，请稍后重试") from exc
    if not rows:
        return 0
    return int(list(rows[0].values())[0])


def stats_row_count() -> int:
    """服务端**元数据视角**的行数（含尚未被检索节点读到的段）。

    只给自检用，而且只和 `count()` **配对**看：
      · `count()`=0 而这里 > 0  →  数据在，但检索读不到（未加载 / 未落盘）
      · 两边都 0                →  真的没数据，该去点向量化
    这两个读数来自不同子系统，所以它们不一致本身就是最有价值的信号。
    """
    client = get_client()
    try:
        stats = client.get_collection_stats(settings.MILVUS_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Milvus collection_stats 失败 err=%s", exc)
        raise AppException(ErrorCode.MILVUS_ERROR, "向量库状态读取失败，请稍后重试") from exc
    return int((stats or {}).get("row_count", 0))


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
        # 同 count()：读失败不能伪装成"库里没有"。返回 [] 会让自检算出
        # "孤儿向量 0 条"，而 purge 流程一旦基于这个假读数做决定就是误删。
        logger.warning("读取 knowledge_base_id 列表失败 err=%s", exc)
        raise AppException(ErrorCode.MILVUS_ERROR, "向量库知识库清单读取失败，请稍后重试") from exc
    return sorted({int(r[F_KB]) for r in rows if r.get(F_KB) is not None})


def list_document_ids() -> list[int]:
    """列出 collection 里出现过的所有 document_id（去重）。

    **同样只给维护/自检用**（理由同 list_kb_ids：要把行取回来才能去重）。
    用途是反向的：找出"文档已经 completed、chunk 也在，但向量一条都没有"的那些文档
    —— 它们检索不到，而从接口和列表上都看不出来。这跟孤儿向量是一对：
    孤儿是"向量多出来了"，这个是"向量缺了"，两种都会静默改变检索结果。
    """
    client = get_client()
    try:
        rows = client.query(
            collection_name=settings.MILVUS_COLLECTION,
            filter="",
            output_fields=[F_DOC],
            consistency_level=CONSISTENCY_LEVEL,
            limit=16384,
        )
    except Exception as exc:  # noqa: BLE001
        # 同 count()：读失败伪装成"什么都没索引过"，自检就会把全部文档报成缺向量
        logger.warning("读取 document_id 列表失败 err=%s", exc)
        raise AppException(ErrorCode.MILVUS_ERROR, "向量库文档清单读取失败，请稍后重试") from exc
    return sorted({int(r[F_DOC]) for r in rows if r.get(F_DOC) is not None})


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
