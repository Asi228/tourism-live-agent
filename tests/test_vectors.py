"""Phase 5 向量检索测试。

**不打真实 Milvus、不打真实 Embedding API** —— 两者都用 monkeypatch 替掉。
原因：单测依赖外部服务会变慢、变脆（Milvus 没开就全红），而且真实连通性用
端到端脚本单独验更合适。

这里只验证**我们自己写的逻辑**：
  - 检索范围的计算（权限收窄）—— 这是本阶段最关键的安全点
  - 字段映射（chunk → Milvus 行）
  - 回库取正文、孤儿向量处理
  - 状态/权限前置校验
"""

import io
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db import session as db_session
from app.models import Document, DocumentChunk, User
from app.rag import vector_store
from app.services import document_service, vector_service

FAKE_VECTOR = [0.01] * settings.EMBEDDING_DIM


@pytest.fixture
def upload_env(tmp_path, monkeypatch, session_factory):
    """上传目录指到临时目录，并让后台解析任务写测试库（同 Phase 4 的做法）。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(db_session, "new_session", session_factory)
    return tmp_path


@pytest.fixture
def mute_milvus(monkeypatch):
    """替掉 Milvus 侧的读写，并记录调用参数与**调用顺序**，便于断言。

    顺序很重要：向量化必须"先按 document_id 清、再 upsert"，
    反过来就会把刚写的向量删掉。
    """
    calls = {
        "upsert": [], "search": [], "delete_doc": [], "delete_kb": [],
        "ensured": 0, "flushed": 0, "order": [],
    }

    def fake_ensure():
        calls["ensured"] += 1
        calls["order"].append("ensure")

    def fake_flush():
        calls["flushed"] += 1
        calls["order"].append("flush")
        return True

    def fake_upsert(rows):
        calls["upsert"].extend(rows)
        calls["order"].append("upsert")
        return len(rows)

    def fake_search(vector, kb_ids, top_k):
        calls["search"].append({"kb_ids": kb_ids, "top_k": top_k})
        calls["order"].append("search")
        return []

    def fake_delete_doc(doc_id):
        calls["delete_doc"].append(doc_id)
        calls["order"].append("delete_doc:%s" % doc_id)
        return 1

    def fake_delete_kb(kb_id):
        calls["delete_kb"].append(kb_id)
        calls["order"].append("delete_kb:%s" % kb_id)
        return 1

    monkeypatch.setattr(vector_store, "ensure_collection", fake_ensure)
    monkeypatch.setattr(vector_store, "upsert", fake_upsert)
    monkeypatch.setattr(vector_store, "search", fake_search)
    monkeypatch.setattr(vector_store, "delete_by_document", fake_delete_doc)
    monkeypatch.setattr(vector_store, "delete_by_kb", fake_delete_kb)
    monkeypatch.setattr(vector_store, "flush", fake_flush)
    return calls


@pytest.fixture
def mute_embedding(monkeypatch):
    """替掉硅基流动调用。"""
    calls = {"texts": [], "query": []}

    def fake_embed_texts(texts):
        calls["texts"].extend(texts)
        return [FAKE_VECTOR for _ in texts]

    def fake_embed_query(text):
        calls["query"].append(text)
        return FAKE_VECTOR

    monkeypatch.setattr(vector_service.embedding, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(vector_service.embedding, "embed_query", fake_embed_query)
    return calls


# ==================== 工具 ====================


def _kb(client, admin_headers, **extra) -> int:
    payload = {"name": "vkb-" + uuid.uuid4().hex[:10], **extra}
    r = client.post("/api/knowledge", json=payload, headers=admin_headers)
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _grant(client, admin_headers, kb_id, user_id, perm):
    r = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": user_id, "permission": perm},
        headers=admin_headers,
    )
    assert r.json()["code"] == 0, r.json()


def _upload(client, headers, kb_id, text: str, name="制度.txt") -> int:
    r = client.post(
        "/api/documents/upload",
        data={"kb_id": kb_id},
        files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
        headers=headers,
    )
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _chunks_of(session_factory, doc_id):
    with session_factory() as db:
        return list(
            db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )


# ==================== 检索范围（安全核心）====================


def test_admin_scope_is_unrestricted(client, make_user, session_factory):
    """系统管理员：不传 kb_ids = 不限；传了就按传的收窄。"""
    admin = make_user("admin")
    with session_factory() as db:
        user = db.get(User, admin["id"])
        assert vector_service._resolve_search_scope(db, user, None) is None
        assert vector_service._resolve_search_scope(db, user, [5, 3, 5]) == [3, 5]


def test_employee_scope_is_intersected_with_permission(client, make_user, session_factory):
    """普通员工：请求里的 kb_ids 只能缩小范围，不能扩大。

    注意：测试库是整个 session 共用的，别的用例建的 is_public 库也会进 scope。
    所以这里不做"等于某个列表"的绝对断言，改为断言"该进的进了、不该进的不进"。
    """
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"])
    other_kb = _kb(client, admin["headers"])
    _grant(client, admin["headers"], kb_id, emp["id"], "read")

    with session_factory() as db:
        user = db.get(User, emp["id"])
        # 只传有权限的 → 结果里就是它
        assert vector_service._resolve_search_scope(db, user, [kb_id]) == [kb_id]
        # 不传 → 用他自己的权限范围，必须包含被授权的库
        assert kb_id in vector_service._resolve_search_scope(db, user, None)
        # **传了别人的库 → 被交集干掉**
        assert vector_service._resolve_search_scope(db, user, [other_kb]) == []
        # 混着传 → 只留下有权限的那个
        assert vector_service._resolve_search_scope(db, user, [kb_id, other_kb]) == [kb_id]


def test_private_kb_not_in_scope_without_grant(client, make_user, session_factory):
    """没被授权的私有库，不能出现在别人的可检索范围里。"""
    admin = make_user("admin")
    emp = make_user("employee")
    private_kb = _kb(client, admin["headers"], is_public=False)

    with session_factory() as db:
        user = db.get(User, emp["id"])
        assert private_kb not in vector_service._resolve_search_scope(db, user, None)
        assert vector_service._resolve_search_scope(db, user, [private_kb]) == []


def test_public_kb_is_in_scope_for_everyone(client, make_user, session_factory):
    """is_public 的库，任何登录用户都能检索到 —— 这条和知识库列表口径一致。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"], is_public=True)

    with session_factory() as db:
        user = db.get(User, emp["id"])
        assert kb_id in vector_service._resolve_search_scope(db, user, None)
        assert vector_service._resolve_search_scope(db, user, [kb_id]) == [kb_id]


def test_empty_scope_never_touches_milvus_or_embedding(
    client, make_user, session_factory, mute_milvus, mute_embedding
):
    """范围被收到空时，必须直接返回空 —— 绝不能退化成"不过滤搜全库"。

    用"显式指定一个自己没权限的库"来制造空范围：这既是真实的越权尝试场景，
    又不受测试库里其他公开知识库的干扰。
    """
    admin = make_user("admin")
    emp = make_user("employee")
    forbidden_kb = _kb(client, admin["headers"], is_public=False)

    with session_factory() as db:
        user = db.get(User, emp["id"])
        out = vector_service.search(db, user, "年假怎么申请", kb_ids=[forbidden_kb], top_k=5)

    assert out["results"] == []
    assert out["searched_kb_ids"] == []
    assert mute_milvus["search"] == []      # 一次 Milvus 都没查
    assert mute_embedding["query"] == []    # 一次 embedding 都没调


# ==================== 检索：范围传递与结果组装 ====================


def test_search_passes_scope_as_milvus_filter(
    client, make_user, session_factory, mute_milvus, mute_embedding
):
    """算出来的范围要原样交给 Milvus 做过滤（而不是捞回来再筛）。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"])
    _grant(client, admin["headers"], kb_id, emp["id"], "read")

    with session_factory() as db:
        user = db.get(User, emp["id"])
        # 显式指定 kb_ids，结果就是它（不受测试库里其他公开库干扰）
        vector_service.search(db, user, "年假", kb_ids=[kb_id], top_k=7)

    assert mute_milvus["search"] == [{"kb_ids": [kb_id], "top_k": 7}]
    assert mute_embedding["query"] == ["年假"]


def test_search_admin_passes_none_filter(
    client, make_user, session_factory, mute_milvus, mute_embedding
):
    admin = make_user("admin")
    with session_factory() as db:
        user = db.get(User, admin["id"])
        vector_service.search(db, user, "年假")
    assert mute_milvus["search"][0]["kb_ids"] is None


def test_search_hydrates_content_and_document_name(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding, monkeypatch
):
    """Milvus 只回 chunk_id，正文和文件名必须回 MySQL 取回来。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度：满一年可休五天。", "员工手册.txt")
    chunks = _chunks_of(session_factory, doc_id)
    assert chunks, "上传后应当产生切分块"

    hit = vector_store.SearchHit(
        chunk_id=chunks[0].id,
        document_id=doc_id,
        knowledge_base_id=kb_id,
        page_number=None,
        score=0.87,
    )
    monkeypatch.setattr(vector_store, "search", lambda *a, **k: [hit])

    with session_factory() as db:
        user = db.get(User, admin["id"])
        out = vector_service.search(db, user, "年假", top_k=1)

    item = out["results"][0]
    assert item["chunk_id"] == chunks[0].id
    assert item["document_name"] == "员工手册.txt"
    assert item["knowledge_base_id"] == kb_id
    assert item["score"] == 0.87
    assert "年假" in item["content"]


def test_search_skips_orphan_vectors(
    client, make_user, session_factory, mute_milvus, mute_embedding, monkeypatch
):
    """Milvus 里还在、MySQL 里已删的向量要被跳过，而不是整条检索崩掉。"""
    admin = make_user("admin")
    hit = vector_store.SearchHit(
        chunk_id=999999999,
        document_id=888888888,
        knowledge_base_id=1,
        page_number=1,
        score=0.5,
    )
    monkeypatch.setattr(vector_store, "search", lambda *a, **k: [hit])

    with session_factory() as db:
        user = db.get(User, admin["id"])
        out = vector_service.search(db, user, "任何问题")

    assert out["results"] == []
    # 跳过数必须报出来：孤儿会白占 top_k 名额、让用户拿到的结果变少，
    # 而这个数字以前只体现在日志里（实测为排查它花过不少时间）
    assert out["skipped_orphans"] == 1


# ==================== 向量化 ====================


def test_vectorize_requires_completed_document(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding, monkeypatch
):
    """文档还没解析完就向量化，应明确报参数错误而不是产生空向量。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容")

    # 正常路径下上传即完成，这里手动把它改回 processing 来触发守卫
    with session_factory() as db:
        doc = db.get(Document, doc_id)
        doc.status = "processing"
        db.commit()

    r = client.post(f"/api/documents/{doc_id}/vectorize", headers=admin["headers"])
    assert r.json()["code"] == 40000
    assert "尚未处理完成" in r.json()["message"]
    assert mute_milvus["upsert"] == []


def test_vectorize_writes_expected_rows(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding
):
    """写入 Milvus 的行必须带上 kb_id / document_id / page_number。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度说明。" * 20)

    r = client.post(f"/api/documents/{doc_id}/vectorize", headers=admin["headers"])
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["collection"] == settings.MILVUS_COLLECTION
    assert body["data"]["vectorized"] == len(_chunks_of(session_factory, doc_id))

    rows = mute_milvus["upsert"]
    assert all(isinstance(x, vector_store.ChunkVector) for x in rows)
    assert {x.knowledge_base_id for x in rows} == {kb_id}
    assert {x.document_id for x in rows} == {doc_id}
    assert len(mute_embedding["texts"]) == len(rows)
    assert mute_milvus["ensured"] == 1  # ensure_collection 只调一次


def test_vectorize_requires_write_permission(client, make_user, upload_env, mute_milvus, mute_embedding):
    """read 权限不能触发向量化（它会写 Milvus）。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容")
    _grant(client, admin["headers"], kb_id, emp["id"], "read")

    r = client.post(f"/api/documents/{doc_id}/vectorize", headers=emp["headers"])
    assert r.json()["code"] == 40400
    assert mute_milvus["upsert"] == []


def test_vectorize_other_users_document_is_404(client, make_user, upload_env, mute_milvus):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容")

    r = client.post(f"/api/documents/{doc_id}/vectorize", headers=emp["headers"])
    assert r.json()["code"] == 40400


def test_vectorize_kb_batch(client, make_user, session_factory, upload_env, mute_milvus, mute_embedding):
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    d1 = _upload(client, admin["headers"], kb_id, "第一份文档内容。" * 10)
    d2 = _upload(client, admin["headers"], kb_id, "第二份文档内容。" * 10)

    r = client.post(f"/api/knowledge/{kb_id}/vectorize", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()

    expected = len(_chunks_of(session_factory, d1)) + len(_chunks_of(session_factory, d2))
    assert r.json()["data"]["vectorized"] == expected


def test_vectorize_kb_rejects_empty_kb(client, make_user, upload_env, mute_milvus):
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    r = client.post(f"/api/knowledge/{kb_id}/vectorize", headers=admin["headers"])
    assert r.json()["code"] == 40000
    assert "没有已处理完成" in r.json()["message"]


# ==================== 孤儿向量（重跑处理 / 重跑向量化）====================


def test_vectorize_purges_old_vectors_before_upsert(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding
):
    """向量化必须**先按 document_id 清旧向量、再写新的**。

    不清的话：文档重跑处理会产生一批新的 chunk_id，而 Milvus 只按主键 upsert，
    旧 chunk_id 的向量会留下来成为孤儿 —— 检索命中却查不到正文，白占 top_k 名额。
    """
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度说明。" * 10)

    r = client.post(f"/api/documents/{doc_id}/vectorize", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()

    # 清理动作确实发生了
    assert doc_id in mute_milvus["delete_doc"]
    # 而且必须排在 upsert 之前，否则会把刚写进去的向量删掉
    order = mute_milvus["order"]
    assert order.index("delete_doc:%s" % doc_id) < order.index("upsert")


def test_vectorize_kb_purges_each_document_before_upsert(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding
):
    """批量向量化时，每份文档也要先清后写。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    d1 = _upload(client, admin["headers"], kb_id, "第一份内容。" * 10)
    d2 = _upload(client, admin["headers"], kb_id, "第二份内容。" * 10)

    r = client.post(f"/api/knowledge/{kb_id}/vectorize", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()

    assert set(mute_milvus["delete_doc"]) == {d1, d2}
    # 每个 delete_doc 都要出现在某次 upsert 之前
    assert mute_milvus["order"].index("delete_doc:%s" % d1) < mute_milvus["order"].index("upsert")


def test_reprocessing_document_purges_its_vectors(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding
):
    """重跑文档处理时必须作废旧向量。

    MySQL 不会复用已删除的主键（实测 document_chunks AUTO_INCREMENT 只增不减），
    所以重跑后 chunk_id 是一批新值；不清向量，Milvus 里就会留下指向旧 chunk_id 的孤儿。
    """
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度说明。" * 10)

    # 上传时已经跑过一次 process_document，这里再跑一次模拟"重跑"
    mute_milvus["order"].clear()
    document_service.process_document(doc_id)

    assert doc_id in mute_milvus["delete_doc"], "重跑处理没有清理旧向量"
    assert mute_milvus["upsert"] == [], "process_document 不该写向量（向量化是独立一步）"


def test_reprocessing_still_succeeds_when_milvus_down(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding, monkeypatch
):
    """Milvus 挂了不能导致"文档处理"失败 —— 向量清理是尽力而为。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度说明。" * 10)

    def boom(doc_id):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(vector_store, "delete_by_document", boom)
    document_service.process_document(doc_id)  # 不应抛异常

    detail = client.get(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["data"]
    assert detail["status"] == "completed"


# ==================== 自检与孤儿清理 ====================


def test_vector_health_requires_admin(client, make_user, mute_milvus):
    emp = make_user("employee")
    r = client.get("/api/vector/health", headers=emp["headers"])
    assert r.json()["code"] == 40300


def test_vector_health_reports_state_without_leaking_host(
    client, make_user, mute_milvus, monkeypatch
):
    """自检要能看见 collection 状态，但**不能把 Milvus 地址返给客户端**。"""
    admin = make_user("admin")
    monkeypatch.setattr(
        vector_store, "health",
        lambda: {"server_version": "2.6.15", "collection": "document_chunks",
                 "collection_exists": True, "collections": ["document_chunks"]},
    )
    monkeypatch.setattr(vector_store, "count", lambda kb_ids=None: 7)
    monkeypatch.setattr(vector_store, "list_kb_ids", lambda: [1, 999999])

    r = client.get("/api/vector/health", headers=admin["headers"])
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["available"] is True
    assert data["server_version"] == "2.6.15"
    assert data["vector_count"] == 7
    # 不存在的 kb_id 要被识别成孤儿
    assert 999999 in data["orphan_kb_ids"]
    # 关键：响应体里不能出现主机/端口
    import json

    text = json.dumps(body, ensure_ascii=False)
    assert settings.MILVUS_HOST not in text
    assert str(settings.MILVUS_PORT) not in text


def test_vector_health_reports_unavailable_instead_of_500(
    client, make_user, mute_milvus, monkeypatch
):
    """Milvus 连不上时，自检应当返回 available=false 而不是 500。"""
    admin = make_user("admin")

    def boom():
        raise AppException(ErrorCode.MILVUS_ERROR, "向量服务暂时不可用，请稍后重试")

    monkeypatch.setattr(vector_store, "health", boom)
    r = client.get("/api/vector/health", headers=admin["headers"])
    assert r.json()["code"] == 0
    assert r.json()["data"]["available"] is False


def test_purge_orphans_requires_admin_and_only_removes_orphans(
    client, make_user, session_factory, mute_milvus, monkeypatch
):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _kb(client, admin["headers"])

    assert client.post("/api/vector/purge-orphans",
                       headers=emp["headers"]).json()["code"] == 40300

    # 一个真实存在的库 + 一个不存在的库
    monkeypatch.setattr(vector_store, "list_kb_ids", lambda: [kb_id, 987654321])
    monkeypatch.setattr(vector_store, "count", lambda kb_ids=None: 1)

    r = client.post("/api/vector/purge-orphans", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["orphan_kb_ids"] == [987654321]
    # 只删孤儿，真实存在的库不能被删
    assert mute_milvus["delete_kb"] == [987654321]
    assert kb_id not in mute_milvus["delete_kb"]


# ==================== Embedding 客户端构建 ====================


def test_embedding_client_disables_tiktoken_preprocessing(monkeypatch):
    """必须带 check_embedding_ctx_length=False。

    开着的话 LangChain 会用 **OpenAI 的 tiktoken** 预处理文本，
    而硅基流动不是 OpenAI，分词规则不同 → 文本被错误切短甚至直接报错。
    这是"用 OpenAI 兼容协议接第三方"的经典坑，钉住它，别被后人"顺手清理掉"。
    """
    import app.rag.embedding as emb

    emb.reset_client()
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "EMBEDDING_BASE_URL", "https://example.invalid/v1")

    client = emb._get_client()
    assert client.check_embedding_ctx_length is False
    assert client.model == settings.EMBEDDING_MODEL
    emb.reset_client()


def test_embedding_raises_clear_error_without_key(monkeypatch):
    import app.rag.embedding as emb

    emb.reset_client()
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    with pytest.raises(AppException) as exc:
        emb._get_client()
    assert exc.value.code == ErrorCode.EMBEDDING_ERROR
    emb.reset_client()


# ==================== filter 表达式 ====================


def test_build_kb_filter_format():
    """filter 表达式的格式要能被 Milvus 解析，且空列表不能变成"匹配全部"。"""
    assert vector_store.build_kb_filter(None) is None
    assert vector_store.build_kb_filter([3, 1, 3]) == "knowledge_base_id in [1, 3]"
    assert vector_store.build_kb_filter([7]) == "knowledge_base_id in [7]"


# ==================== 清理联动 ====================


def test_deleting_document_purges_vectors(client, make_user, upload_env, mute_milvus, mute_embedding):
    """删文档要顺手清掉它的向量。

    注意这里**会被清两次**：一次是上传时的 process_document（作废旧向量），
    一次是 delete_document（删文档）。两次都删同一个 document_id 是幂等的，不算问题 ——
    Milvus 的 delete 是"按表达式删"，没有匹配行时也不会报错。
    """
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容")

    assert client.delete(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["code"] == 0
    assert doc_id in mute_milvus["delete_doc"]
    # 删除动作必须发生在最后（处理阶段那次更早）。
    # flush 是删除之后的落盘，不算"又动了一次向量"，所以先把它剔出去再比。
    moves = [x for x in mute_milvus["order"] if x != "flush"]
    assert moves[-1] == "delete_doc:%s" % doc_id


def test_deleting_kb_purges_vectors(client, make_user, upload_env, mute_milvus):
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])

    assert client.delete(f"/api/knowledge/{kb_id}", headers=admin["headers"]).json()["code"] == 0
    assert mute_milvus["delete_kb"] == [kb_id]


def test_vector_purge_failure_does_not_block_delete(
    client, make_user, upload_env, mute_milvus, monkeypatch
):
    """Milvus 挂了不能让"删文档"失败 —— 否则会出现删不掉的文档。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容")

    def boom(doc_id):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(vector_store, "delete_by_document", boom)

    r = client.delete(f"/api/documents/{doc_id}", headers=admin["headers"])
    assert r.json()["code"] == 0


# ==================== 「数据在不在」与「读不读得到」必须分开 ====================
#
# 2026-10-14 实测：Milvus 2.6.15 上出现过 `get_load_state()=Loaded` 但 search/count
# 一律返回空的状况 —— 那 10 条向量用主键点查查得到，检索却读不到。
# 当时的自检因此报"向量总数 0、3 篇文档全缺向量"，把人引去重灌**根本没丢**的数据。
# 下面这组用例守的就是：这种状态必须被说成 not_visible，而且**不许**报成缺向量。


def _stub_reads(monkeypatch, **over):
    """把 self_check 用到的五个读数钉成固定值（默认"一切正常"）。"""
    values = {
        "count": 10,
        "stats_row_count": 10,
        "list_kb_ids": [1],
        "list_document_ids": [1],
    }
    values.update(over)
    for name, value in values.items():
        if callable(value):
            monkeypatch.setattr(vector_store, name, value)
        else:
            monkeypatch.setattr(vector_store, name, lambda *a, v=value, **kw: v)


def test_count_raises_instead_of_disguising_read_failure_as_zero(monkeypatch):
    """🔴 读失败必须抛，不能 `return 0`。0 到了自检里就是"一条向量都没有"。"""

    class Boom:
        def query(self, **kwargs):
            raise RuntimeError("collection not loaded")

    monkeypatch.setattr(vector_store, "get_client", lambda: Boom())
    with pytest.raises(AppException) as exc:
        vector_store.count()
    assert exc.value.code == ErrorCode.MILVUS_ERROR


def test_stats_row_count_reads_the_server_side_number(monkeypatch):
    """服务端元数据视角的行数（含未压缩的删除记录），只用来判"有没有数据"。"""

    class Fake:
        def get_collection_stats(self, name):
            return {"row_count": 27}

    monkeypatch.setattr(vector_store, "get_client", lambda: Fake())
    assert vector_store.stats_row_count() == 27


def test_ensure_collection_loads_an_existing_collection(monkeypatch):
    """集合已存在时也要 load —— 旧实现直接 return，应用因此永远不会自愈。"""
    loaded = []

    class Fake:
        def has_collection(self, name):
            return True

        def load_collection(self, collection_name):
            loaded.append(collection_name)

    monkeypatch.setattr(vector_store, "get_client", lambda: Fake())
    vector_store.ensure_collection()
    assert loaded == [settings.MILVUS_COLLECTION], "已存在就跳过 load，读不到的集合永远好不了"


def test_ensure_loaded_swallows_failure_because_it_is_a_self_heal():
    """load 失败不能把一次本来能成功的向量化打挂 —— 只记日志、返回 False。"""

    class Boom:
        def has_collection(self, name):
            return True

        def load_collection(self, collection_name):
            raise RuntimeError("querynode down")

    assert vector_store._load_into_querynode(Boom(), settings.MILVUS_COLLECTION) is False


def test_vectorize_flushes_after_writing(
    client, make_user, session_factory, upload_env, mute_milvus, mute_embedding
):
    """写完必须 flush：不落盘的向量在 Milvus 重启后对检索就是不可见的。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "年假制度说明。" * 20)

    body = client.post(f"/api/documents/{doc_id}/vectorize", headers=admin["headers"]).json()
    assert body["code"] == 0, body
    assert body["data"]["flushed"] is True
    # 只看写入这一段：上传时的"处理"也会清一次向量（因此也 flush 过一次），
    # 这里钉的是**upsert 之后必须紧跟 flush**，而不是"整个生命周期只 flush 一次"。
    writes = [x for x in mute_milvus["order"] if x in ("upsert", "flush")]
    assert writes[-2:] == ["upsert", "flush"], "flush 必须在写入之后，否则重启后读不到"


def test_vectorize_reports_flush_failure_without_losing_the_write(
    client, make_user, upload_env, mute_milvus, mute_embedding, monkeypatch
):
    """flush 失败时：写入这件事仍然成立，但必须**当场说出来**，不能报"完成"。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容" * 30)

    monkeypatch.setattr(vector_store, "flush", lambda: False)
    body = client.post(f"/api/documents/{doc_id}/vectorize", headers=admin["headers"]).json()
    assert body["code"] == 0
    assert body["data"]["vectorized"] >= 1        # 数据确实写了
    assert body["data"]["flushed"] is False       # 但没落盘
    assert "未落盘" in body["message"]


def test_self_check_calls_hidden_vectors_not_visible(
    client, make_user, upload_env, mute_milvus, monkeypatch
):
    """核心回归：检索可见 0 条、服务端却有数据 → 报 not_visible，且**不许**报成缺向量。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容" * 30)

    monkeypatch.setattr(
        vector_store, "health",
        lambda: {"server_version": "2.6.15", "collection": "document_chunks",
                 "collection_exists": True, "collections": ["document_chunks"]},
    )
    _stub_reads(monkeypatch, count=0, stats_row_count=27,
                list_kb_ids=[kb_id], list_document_ids=[doc_id])

    data = client.get("/api/vector/health", headers=admin["headers"]).json()["data"]
    assert data["visibility"] == "not_visible", data
    assert data["vector_count"] == 0
    assert data["stored_rows"] == 27
    # 🔴 向量其实一条都不缺 —— 旧实现会把这里算成 1（因为 list_document_ids 读失败返回 []）
    assert data["documents_without_vectors"] == 0


def test_self_check_reports_missing_only_when_there_is_really_nothing(
    client, make_user, upload_env, mute_milvus, monkeypatch
):
    """对照组：两边都是 0 才是真的缺向量，这时才该建议去点批量向量化。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    _upload(client, admin["headers"], kb_id, "内容" * 30)

    monkeypatch.setattr(
        vector_store, "health",
        lambda: {"server_version": "2.6.15", "collection": "document_chunks",
                 "collection_exists": True, "collections": ["document_chunks"]},
    )
    _stub_reads(monkeypatch, count=0, stats_row_count=0, list_kb_ids=[], list_document_ids=[])

    data = client.get("/api/vector/health", headers=admin["headers"]).json()["data"]
    assert data["visibility"] == "missing", data
    assert data["documents_without_vectors"] == 1
    assert "向量化" in data["visibility_hint"]


def test_self_check_survives_a_failed_read_and_says_so(
    client, make_user, upload_env, mute_milvus, monkeypatch
):
    """读数取不到时：接口不能 500，数字要成 null，并明确"结论不可信"。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    _upload(client, admin["headers"], kb_id, "内容" * 30)

    monkeypatch.setattr(
        vector_store, "health",
        lambda: {"server_version": "2.6.15", "collection": "document_chunks",
                 "collection_exists": True, "collections": ["document_chunks"]},
    )

    def boom(**kwargs):
        raise AppException(ErrorCode.MILVUS_ERROR, "向量条数统计失败，请稍后重试")

    _stub_reads(monkeypatch, count=boom, stats_row_count=boom)

    r = client.get("/api/vector/health", headers=admin["headers"])
    assert r.json()["code"] == 0, "自检接口不该因为一个读数失败就整体报错"
    data = r.json()["data"]
    assert data["vector_count"] is None, "读失败必须显示成 null，而不是 0"
    assert data["visibility"] == "unknown"
    assert data["read_errors"], "要留下「哪个读数取失败」的痕迹"


def test_admin_overview_shows_dash_instead_of_zero_when_count_fails(
    client, make_user, mute_milvus, monkeypatch
):
    """概览页同一口径：向量条数读不到时给 null（前端显示"—/不可用"），不是 0。"""
    admin = make_user("admin")

    def boom():
        raise AppException(ErrorCode.MILVUS_ERROR, "向量条数统计失败")

    monkeypatch.setattr(vector_store, "count", boom)
    body = client.get("/api/stats/overview", headers=admin["headers"]).json()
    assert body["code"] == 0
    assert body["data"]["admin"]["vector_count"] is None


# ==================== 落盘要覆盖删除，同实例陌生集合要指出来 ====================


def test_flush_never_raises_even_when_milvus_is_unreachable():
    """🔴 flush 的契约是 True/False，不是抛异常。

    否则会出现最难受的一种失败：**向量已经写成功了**，却因为随后那一步落盘连不上
    而让整个向量化请求报错 —— 用户看到失败，数据其实进去了。
    测的是执行体 `_flush_best_effort`：`flush` 这个名字在测试里会被 conftest 的
    写操作哑元替掉，直接调它测到的不是真实现。
    """
    def boom():
        raise AppException(ErrorCode.MILVUS_ERROR, "向量服务暂时不可用，请稍后重试")

    assert vector_store._flush_best_effort(boom, "document_chunks") is False

    class Ok:
        def flush(self, name):
            return None

    assert vector_store._flush_best_effort(lambda: Ok(), "document_chunks") is True


def test_deleting_a_document_flushes_the_removal(
    client, make_user, upload_env, mute_milvus
):
    """删除同样要落盘：不落盘的删除记录重启后可能没生效，向量会"复活"成孤儿。"""
    admin = make_user("admin")
    kb_id = _kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "内容" * 30)

    assert client.delete(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["code"] == 0
    order = mute_milvus["order"]
    # 上传时的"处理"也会清一次向量，所以钉的是**最后两步**：删除 → 紧跟落盘
    assert order[-2:] == ["delete_doc:%s" % doc_id, "flush"], \
        "删除记录没落盘 → 重启后向量会复活成孤儿"


def test_foreign_collections_are_reported_not_dropped(monkeypatch):
    """同实例里结构不对的集合必须被点名（它有行数，但本应用永远读不到）。"""
    class Fake:
        def list_collections(self):
            return ["document_chunks", "docs", "broken"]

        def describe_collection(self, name):
            if name == "broken":
                raise RuntimeError("describe failed")
            fields = ["chunk_id", "vector"] if name == "document_chunks" else ["id", "vector"]
            return {"fields": [{"name": f} for f in fields]}

    monkeypatch.setattr(vector_store, "get_client", lambda: Fake())
    got = vector_store.foreign_collections()
    assert got == ["docs"], "只有主键不是 chunk_id 的才算陌生集合；describe 失败的要跳过"


def test_self_check_surfaces_foreign_collections(
    client, make_user, mute_milvus, monkeypatch
):
    admin = make_user("admin")
    monkeypatch.setattr(
        vector_store, "health",
        lambda: {"server_version": "2.6.15", "collection": "document_chunks",
                 "collection_exists": True, "collections": ["document_chunks", "docs"]},
    )
    _stub_reads(monkeypatch)
    monkeypatch.setattr(vector_store, "foreign_collections", lambda: ["docs"])

    data = client.get("/api/vector/health", headers=admin["headers"]).json()["data"]
    assert data["foreign_collections"] == ["docs"]
