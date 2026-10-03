"""Phase 5 集成测试：**真连 Milvus**，钉住客户端与真实服务端的交互细节。

默认跳过。要跑：

    PowerShell:  $env:RUN_MILVUS_INTEGRATION="1"; python -m pytest tests/test_vectors_integration.py -v

为什么单开一个文件、默认不跑：
  它依赖外部服务（VM 上的 Milvus）。放进主测试集会让"Milvus 没开机"变成一堆红，
  别人 clone 下来第一件事就是测试全挂 —— 那是很差的第一印象。

它盯的是**单元测试盯不到的东西**（用哑元替身时这些都测不出来）：
  1. pymilvus 3.x 的 search 命中结构：主键在**字段名**下（`item["chunk_id"]`），
     不是 2.x 的 `item["id"]` —— 这个坑真的踩过，`int(item.get("id"))` 直接 TypeError
  2. page_number 存 None 能不能原样读回来（schema 声明的是 nullable=True）
  3. knowledge_base_id 的过滤在真实服务端上是否真的生效
  4. 删除后条数是否真的下降（consistency_level=Strong 的实际效果）

不调用真实 Embedding：向量用固定值造，省一次计费、也少一个外部依赖。
"""

import os
import uuid

import pytest

from app.config import settings
from app.rag import vector_store

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MILVUS_INTEGRATION") != "1",
    reason="需要真实 Milvus；设 RUN_MILVUS_INTEGRATION=1 才跑",
)

# 用一个不会和真实数据撞车的高位 kb_id 区间，跑完就删
KB_A = 900000001
KB_B = 900000002
DIM = settings.EMBEDDING_DIM


def _vec(seed: float) -> list[float]:
    return [seed] * DIM


def _cid() -> int:
    """造一个不会和真实 chunk_id 撞的高位主键。"""
    return 900000000 + (uuid.uuid4().int % 1000000)


@pytest.fixture(scope="module")
def milvus():
    vector_store.ensure_collection()
    info = vector_store.health()
    print("\nMilvus 服务端版本 = %s, collections = %s" % (info["server_version"], info["collections"]))
    yield
    # 收尾：把本文件造的数据清掉，别在别人库里留垃圾
    vector_store.delete_by_kb(KB_A)
    vector_store.delete_by_kb(KB_B)


def test_server_roundtrip_and_primary_key_field_name(milvus):
    """写入后能搜到，且主键能正确解析 —— 这条就是钉住 pymilvus 3.x 的结构变化。"""
    cid = _cid()
    vector_store.upsert(
        [
            vector_store.ChunkVector(
                chunk_id=cid,
                document_id=900000777,
                knowledge_base_id=KB_A,
                page_number=3,
                vector=_vec(0.1),
            )
        ]
    )

    hits = vector_store.search(_vec(0.1), [KB_A], top_k=5)
    assert hits, "写入后应当能检索到（consistency_level=Strong 保证立即可见）"

    matched = [h for h in hits if h.chunk_id == cid]
    assert matched, (
        "主键解析失败：hits 里的 chunk_id = %s，期望含 %s。"
        "pymilvus 3.x 把主键放在字段名下（item['chunk_id']），不是 item['id']"
        % ([h.chunk_id for h in hits], cid)
    )
    hit = matched[0]
    assert hit.knowledge_base_id == KB_A
    assert hit.document_id == 900000777
    assert hit.page_number == 3
    assert hit.score > 0.9  # 同一个向量，余弦应该接近 1


def test_page_number_null_roundtrip(milvus):
    """page_number 为 None（txt/md 没有页概念）要能原样读回来。

    schema 建的是 nullable=True。之前代码里把 None 存成 0 并注释说"Milvus 不接受 null"，
    那句是没验证就写下的断言 —— 这条测试就是用来证伪/证实它的。
    """
    cid = _cid()
    vector_store.upsert(
        [
            vector_store.ChunkVector(
                chunk_id=cid,
                document_id=900000778,
                knowledge_base_id=KB_A,
                page_number=None,
                vector=_vec(0.2),
            )
        ]
    )
    hits = [h for h in vector_store.search(_vec(0.2), [KB_A], top_k=5) if h.chunk_id == cid]
    assert hits, "写入后应当能检索到"
    assert hits[0].page_number is None, (
        "page_number 存 None 后读回来成了 %r —— null 没被保住" % hits[0].page_number
    )


def test_kb_filter_really_filters_on_server(milvus):
    """过滤表达式在真实服务端上必须真的生效（这是权限隔离的最后一环）。"""
    cid_a, cid_b = _cid(), _cid()
    vector_store.upsert(
        [
            vector_store.ChunkVector(cid_a, 900000779, KB_A, 1, _vec(0.3)),
            vector_store.ChunkVector(cid_b, 900000780, KB_B, 2, _vec(0.3)),
        ]
    )

    only_a = vector_store.search(_vec(0.3), [KB_A], top_k=10)
    assert all(h.knowledge_base_id == KB_A for h in only_a), "过滤没生效，串到了别的库"
    assert cid_b not in [h.chunk_id for h in only_a]

    both = vector_store.search(_vec(0.3), [KB_A, KB_B], top_k=10)
    got = {h.chunk_id for h in both}
    assert {cid_a, cid_b} <= got


def test_empty_kb_ids_never_means_search_everything(milvus):
    """空列表绝不能退化成"不过滤" —— 那等于越权搜全库。"""
    assert vector_store.search(_vec(0.3), [], top_k=5) == []


def test_delete_by_document_and_kb_makes_rows_disappear(milvus):
    """删除后条数要真的下降（验证 consistency_level=Strong 的实际效果）。"""
    cid = _cid()
    doc_id = 900000781
    vector_store.upsert(
        [vector_store.ChunkVector(cid, doc_id, KB_A, 1, _vec(0.4))]
    )
    assert vector_store.count([KB_A]) >= 1

    vector_store.delete_by_document(doc_id)
    remaining = [h for h in vector_store.search(_vec(0.4), [KB_A], top_k=10)
                 if h.chunk_id == cid]
    assert remaining == [], "删掉的向量仍然能被检索到"

    vector_store.delete_by_kb(KB_B)
    assert vector_store.count([KB_B]) == 0
