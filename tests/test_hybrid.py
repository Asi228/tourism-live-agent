"""混合检索（关键词路 + RRF 融合）的测试。

三层分开测：
  · 纯函数 —— `rrf_merge`（名次融合）与 `extract_keywords`（jieba 提词）；
  · 编排 —— `search()` 里两路如何汇合（替掉 Milvus 与关键词路，只看"怎么合"）；
  · 真表 —— 关键词路本身（LIKE / 权限 / 状态 / 转义）与"向量全空、关键词兜住"。

为什么单开文件：混合检索改的是**检索主链路**，失败模式全是静默的 ——
融合顺序错、关键词路越权、转义漏了……都不会报错，只会让答案悄悄变差。
"""

import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import STATUS_COMPLETED, Document, DocumentChunk, KnowledgeBase, User
from app.rag import vector_store
from app.rag.fusion import rrf_merge
from app.rag.keyword import extract_keywords
from app.repositories import document_chunk_repository
from app.services import vector_service

# search() 只把 user 用于日志和权限范围；本文件的**编排**测试把权限那步替掉了，
# 所以给个带 username 的占位对象就够（传 None 会挂在日志那一行）。
_USER = SimpleNamespace(username="tester")


def _hit(chunk_id: int, score: float) -> vector_store.SearchHit:
    return vector_store.SearchHit(
        chunk_id=chunk_id, document_id=1, knowledge_base_id=1,
        page_number=None, score=score,
    )


# ==================== rrf_merge（纯函数） ====================


def test_rrf_single_list_keeps_order_and_decays():
    """单路时融合 = 原顺序（这是"关键词路空跑对融合透明"的依据）。"""
    out = rrf_merge([[3, 1, 2]])
    assert [i for i, _ in out] == [3, 1, 2]
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True)


def test_rrf_empty_lists():
    assert rrf_merge([]) == []
    assert rrf_merge([[], []]) == []


def test_rrf_rewards_agreement_between_paths():
    """两路都往前排的结果，必须赢过只有一路靠前的结果 —— 这是融合的意义。"""
    out = rrf_merge([[1, 2, 3], [2, 1, 4]])
    scores = dict(out)
    # 2：两路 rank2+rank1；1：两路 rank1+rank2 —— 两者都远高于只在单路第三的 3/4
    assert scores[1] > scores[3]
    assert scores[2] > scores[4]
    ids = [i for i, _ in out]
    assert set(ids[:2]) == {1, 2}
    assert set(ids[2:]) == {3, 4}


def test_rrf_is_deterministic_on_ties():
    """同分时按 id 升序兜底 —— 两次调用、两路互换顺序，结果必须一致。"""
    assert rrf_merge([[5, 6], [6, 5]]) == rrf_merge([[6, 5], [5, 6]])


def test_rrf_score_accumulates_for_duplicates():
    """同一个 id 出现在多路 → 分数累加（1/61 + 1/61）。"""
    out = rrf_merge([[7], [7]])
    assert len(out) == 1
    assert out[0][0] == 7
    assert out[0][1] == pytest.approx(2 / 61)


# ==================== extract_keywords（jieba） ====================


def test_extract_keywords_keeps_domain_words():
    """🔴 领域词表的回归：jieba 通用词典把"年假"切成 ['年', '假']（实测），
    单字又被过滤 → 没有词表的话这句话只剩"几天"。这条用例钉住词表还在。"""
    words = extract_keywords("年假几天？怎么申请")
    assert "年假" in words, "领域词表失效了 —— 公司制度词汇被切成单字"
    assert "申请" in words
    assert "怎么" not in words  # 停用词


def test_extract_keywords_filters_stopwords_and_single_chars():
    assert extract_keywords("的 了 是 我") == []
    assert extract_keywords("？？。！") == []
    assert extract_keywords("") == []


def test_extract_keywords_keeps_technical_tokens():
    """编号/型号正是关键词路存在的理由 —— 它们不该被过滤器误杀。"""
    words = extract_keywords("ISO27001 与 SQL-2000")
    assert "ISO27001" in words
    assert "2000" in words


def test_extract_keywords_dedupes_and_limits():
    words = extract_keywords("年假 年假 年假 报销 报销", limit=2)
    assert words == ["年假", "报销"]


# ==================== search() 的融合编排（替掉外部依赖） ====================


@pytest.fixture
def env(monkeypatch):
    """替掉权限 / embedding / Milvus / hydrate —— 只留"两路怎么合"这一件事。"""
    box: dict = {"hydrate_ids": None}

    monkeypatch.setattr(vector_service, "_resolve_search_scope", lambda db, user, kb_ids: [1])
    monkeypatch.setattr(
        vector_service.embedding, "embed_query", lambda q: [0.0] * settings.EMBEDDING_DIM
    )

    def fake_hydrate(db, hits):
        box["hydrate_ids"] = [h.chunk_id for h in hits]
        return (
            [
                {
                    "chunk_id": h.chunk_id, "document_id": 1, "document_name": "d.txt",
                    "knowledge_base_id": 1, "page_number": None,
                    "score": h.score, "content": "正文",
                }
                for h in hits
            ],
            0,
        )

    monkeypatch.setattr(vector_service, "_hydrate", fake_hydrate)
    return box


def test_fusion_order_feeds_hydrate_and_vector_score_wins(env, monkeypatch):
    """融合两路名次 → 按融合顺序进 hydrate；两路都有时保留**向量余弦分**。"""
    monkeypatch.setattr(
        vector_service.vector_store, "search", lambda v, s, k: [_hit(1, 0.9), _hit(2, 0.8)]
    )
    monkeypatch.setattr(
        vector_service, "_keyword_recall", lambda db, q, s, l: [_hit(2, 0.5), _hit(3, 0.5)]
    )

    out = vector_service.search(None, _USER, "q", top_k=3)

    # 2 两路都靠前（rank2+rank1）→ 排第一；1 与 3 各只在一路，按 rrf 分
    assert env["hydrate_ids"] == [2, 1, 3]
    scores = {r["chunk_id"]: r["score"] for r in out["results"]}
    assert scores[2] == 0.8, "两路都有时应保留向量余弦分（不是覆盖率）"
    assert scores[3] == 0.5, "仅关键词命中的项用覆盖率分"


def test_hybrid_disabled_skips_keyword_path(env, monkeypatch):
    monkeypatch.setattr(settings, "RAG_HYBRID_ENABLED", False)
    called: list = []
    monkeypatch.setattr(vector_service, "_keyword_recall", lambda *a: called.append(1) or [])
    monkeypatch.setattr(vector_service.vector_store, "search", lambda v, s, k: [_hit(1, 0.9)])

    vector_service.search(None, _USER, "q", top_k=3)

    assert called == [], "开关关了还去跑关键词路"
    assert env["hydrate_ids"] == [1]


def test_empty_keyword_path_degrades_to_vector_order(env, monkeypatch):
    """关键词路空跑（提不出词 / 没命中）不该改变向量路顺序 —— 融合对空路透明。"""
    monkeypatch.setattr(
        vector_service.vector_store, "search",
        lambda v, s, k: [_hit(1, 0.9), _hit(2, 0.8), _hit(3, 0.7)],
    )
    monkeypatch.setattr(vector_service, "_keyword_recall", lambda db, q, s, l: [])

    vector_service.search(None, _USER, "q", top_k=3)

    assert env["hydrate_ids"] == [1, 2, 3]


def test_fused_candidates_capped_to_budget(env, monkeypatch):
    """两路各 2-3 条，融合后仍受候选预算（不 rerank 时 = top_k）约束。"""
    monkeypatch.setattr(
        vector_service.vector_store, "search", lambda v, s, k: [_hit(1, 0.9), _hit(2, 0.8)]
    )
    monkeypatch.setattr(
        vector_service, "_keyword_recall",
        lambda db, q, s, l: [_hit(3, 0.5), _hit(4, 0.5), _hit(5, 0.5)],
    )

    out = vector_service.search(None, _USER, "q", top_k=3)

    assert len(env["hydrate_ids"]) == 3
    assert len(out["results"]) == 3


def test_retrieval_path_marks_recall_origin(env, monkeypatch):
    """每条结果带**召回来源**（vector / keyword / both）—— 两条路的分数语义不同。

    仅关键词命中的 score 是词项覆盖率（不是余弦相似度），展示层据此显示「关键词匹配」；
    没有这个标注就只能统一叫"相似度"，把覆盖率当相似度是假精度（评审 2026-10-15）。
    """
    monkeypatch.setattr(
        vector_service.vector_store, "search", lambda v, s, k: [_hit(1, 0.9), _hit(2, 0.8)]
    )
    monkeypatch.setattr(
        vector_service, "_keyword_recall", lambda db, q, s, l: [_hit(2, 0.5), _hit(3, 0.5)]
    )

    out = vector_service.search(None, _USER, "q", top_k=3)

    paths = {r["chunk_id"]: r["retrieval_path"] for r in out["results"]}
    assert paths == {2: "both", 1: "vector", 3: "keyword"}


# ==================== 关键词路（真表） ====================


def _seed_doc(
    session_factory, owner_id, filename, contents, status=STATUS_COMPLETED, kb_id=None
):
    """造「知识库 → 文档 → 多块」。返回 (kb_id, doc_id, [chunk_id...])。"""
    with session_factory() as db:
        if kb_id is None:
            kb = KnowledgeBase(
                name="kb-" + uuid.uuid4().hex[:10], description="测试", owner_id=owner_id
            )
            db.add(kb)
            db.flush()
            kb_id = kb.id
        doc = Document(
            knowledge_base_id=kb_id,
            uploader_id=owner_id,
            filename=filename,
            stored_name=uuid.uuid4().hex + ".md",
            file_path="/tmp/x.md",
            file_type="md",
            file_size=100,
            status=status,
            chunk_count=len(contents),
            char_count=sum(len(c) for c in contents),
        )
        db.add(doc)
        db.flush()
        ids = []
        for i, text in enumerate(contents):
            ch = DocumentChunk(
                document_id=doc.id, chunk_index=i, content=text, char_count=len(text)
            )
            db.add(ch)
            db.flush()
            ids.append(ch.id)
        db.commit()
        return kb_id, doc.id, ids


def test_repo_orders_by_hit_count(make_user, session_factory):
    """命中词数多的排前面；hit_count 原样带出（它是"覆盖多久"的判据）。"""
    admin = make_user("admin")
    kb_id, _, ids = _seed_doc(
        session_factory, admin["id"], "考勤.md",
        ["年假满一年 5 天。", "年假 10 天。"],
    )
    with session_factory() as db:
        rows = document_chunk_repository.search_by_keywords(db, ["年假", "满一年"], [kb_id], 10)

    assert [c.id for c, _ in rows] == [ids[0], ids[1]]
    assert [n for _, n in rows] == [2, 1]


def test_repo_requires_completed_document(make_user, session_factory):
    """处理中的文档不能被关键词路召回（口径与向量路/文档任务一致）。"""
    from app.models.document import STATUS_PROCESSING

    admin = make_user("admin")
    kb_id, _, _ = _seed_doc(
        session_factory, admin["id"], "年假.md", ["年假 5 天。"], status=STATUS_PROCESSING,
    )
    with session_factory() as db:
        rows = document_chunk_repository.search_by_keywords(db, ["年假"], [kb_id], 10)

    assert rows == []


def test_repo_respects_scope(make_user, session_factory):
    """kb_ids 是"权限求过交集"的范围，关键词路必须遵守它 —— 不能成为越权旁路。"""
    admin = make_user("admin")
    kb_a, _, ids_a = _seed_doc(session_factory, admin["id"], "A.md", ["年假 5 天。"])
    _kb_b, _, ids_b = _seed_doc(session_factory, admin["id"], "B.md", ["年假 10 天。"])

    with session_factory() as db:
        rows = document_chunk_repository.search_by_keywords(db, ["年假"], [kb_a], 10)

    assert [c.id for c, _ in rows] == [ids_a[0]]
    assert ids_b[0] not in [c.id for c, _ in rows]


def test_repo_escapes_like_wildcards(make_user, session_factory):
    """🔴 用户输入里的 % _ 必须转义成字面量。

    不转义的话 `100%` 会变成通配符，把**所有**块都捞回来 ——
    一个安静的越权式误召回（结果变多、变杂，却没有任何报错）。
    """
    admin = make_user("admin")
    kb_id, _, ids = _seed_doc(
        session_factory, admin["id"], "报销.md",
        ["报销比例 100% 封顶。", "无关内容。"],
    )
    with session_factory() as db:
        rows = document_chunk_repository.search_by_keywords(db, ["100%"], [kb_id], 10)

    assert [c.id for c, _ in rows] == [ids[0]], "% 没被转义，通配匹配把无关块也捞回来了"


# ==================== 端到端：向量路失灵时，关键词路兜住 ====================


def test_keyword_path_catches_vector_miss(
    client, make_user, session_factory, monkeypatch
):
    """🔴 混合检索的核心价值：**向量路什么都没召回时，关键词路兜住**。

    模拟"Milvus 侧空了"（历史真出现过：re-ingest 之前检索全空）——
    只要 MySQL 里有字面命中的块，结果就不该是空。
    """
    admin = make_user("admin")
    _kb_id, _doc_id, ids = _seed_doc(
        session_factory, admin["id"], "信息安全规范.md",
        ["系统密码必须 12 位以上，每 90 天更换一次。"],
    )
    monkeypatch.setattr(vector_service.vector_store, "search", lambda v, s, k: [])

    with session_factory() as db:
        user = db.get(User, admin["id"])
        out = vector_service.search(db, user, "密码 12 位", top_k=5)

    assert out["results"], "向量路空 + 关键词命中 → 结果不该为空（混合检索的核心价值）"
    assert out["results"][0]["chunk_id"] == ids[0]
    assert "12 位" in out["results"][0]["content"]


def test_keyword_path_respects_permissions(
    client, make_user, session_factory, monkeypatch
):
    """关键词路不是权限旁路：员工搜自己无权库的内容，结果必须是空。"""
    admin = make_user("admin")
    emp = make_user()  # 无任何库权限
    _seed_doc(session_factory, admin["id"], "机密.md", ["机密：年假 99 天。"])
    monkeypatch.setattr(vector_service.vector_store, "search", lambda v, s, k: [])

    with session_factory() as db:
        user = db.get(User, emp["id"])
        out = vector_service.search(db, user, "年假", top_k=5)

    assert out["results"] == [], "员工通过关键词路看到了无权库的内容"


# ==================== 启动预热与配置校验 ====================


def test_lifespan_warms_up_jieba(monkeypatch):
    """启动时把 jieba 词典建好 —— 否则混合检索的**第一个提问**白等一次构建。

    实测首词构建 ~0.8s（之后 0）。这条钉住 lifespan 里确实调了 warmup：
    谁把预热删掉（比如"顺手清理没被引用的导入"），它会立刻红。
    """
    from fastapi.testclient import TestClient

    from app import create_app
    from app.rag import keyword as keyword_mod

    monkeypatch.setattr(settings, "RAG_HYBRID_ENABLED", True)
    calls: list = []
    monkeypatch.setattr(keyword_mod, "warmup", lambda: calls.append("warmup"))

    # `with` 才触发 lifespan（conftest 的 client 刻意不用 with —— 见它的注释）；
    # ensure_loaded 已被 _guard_real_milvus 换成哑元，这里不会碰真 Milvus。
    with TestClient(create_app()):
        pass

    assert calls == ["warmup"], "lifespan 没有预热 jieba（第一个提问会白等一次词典构建）"


def test_keyword_k_must_be_positive():
    """RAG_KEYWORD_K=0 会让关键词路静默查空（LIMIT 0）—— 与其它 RAG 参数同款，启动拦截。"""
    import pydantic

    from app.config import Settings

    with pytest.raises(pydantic.ValidationError):
        Settings(RAG_KEYWORD_K=0)
    with pytest.raises(pydantic.ValidationError):
        Settings(RAG_KEYWORD_K=-1)
