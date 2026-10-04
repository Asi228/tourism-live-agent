"""Phase 7 重排的测试。

重排真正的风险**不在"排得准不准"**（那是模型的事），而在工程行为：
  失败能不能降级？开关能不能回退？候选数对不对？上游返回越界下标会不会错位到别的文档？
所以下面的用例几乎都在盯这些，而不是盯分数。

三条外部依赖全部替掉：embedding、Milvus、reranker —— 一次真实请求都不打。
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.rag import reranker
from app.services import vector_service

# search() 内部只把 user 用于日志和算权限范围；权限那步在本文件被 mock 掉了，
# 所以这里给个带 username 的占位对象就够（传 None 会挂在日志那一行）。
_USER = SimpleNamespace(username="tester")


def _hit(chunk_id: int, score: float, name: str = "员工手册.txt") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": 1,
        "document_name": name,
        "knowledge_base_id": 1,
        "page_number": None,
        "score": score,
        "content": "候选正文 %d" % chunk_id,
    }


@pytest.fixture
def env(monkeypatch):
    """把检索链路上的外部依赖全部替掉，只留重排的编排逻辑。"""
    box: dict = {"candidate_k": [], "rerank": [], "results": [], "order": [], "error": None}

    monkeypatch.setattr(vector_service, "_resolve_search_scope", lambda db, user, kb_ids: [1])
    monkeypatch.setattr(
        vector_service.embedding, "embed_query", lambda q: [0.0] * settings.EMBEDDING_DIM
    )

    def fake_milvus(vec, scope, k):
        box["candidate_k"].append(k)
        return []

    def fake_hydrate(db, hits):
        # Phase 7 起 _hydrate 返回 (结果, 被跳过的孤儿数)
        return [dict(r) for r in box["results"]], box.get("skipped_orphans", 0)

    def fake_rerank(query, documents, top_n):
        box["rerank"].append({"query": query, "docs": documents, "top_n": top_n})
        if box["error"] is not None:
            raise box["error"]
        return box["order"]

    monkeypatch.setattr(vector_service.vector_store, "search", fake_milvus)
    monkeypatch.setattr(vector_service, "_hydrate", fake_hydrate)
    monkeypatch.setattr(vector_service.reranker, "rerank", fake_rerank)
    return box


# ==================== 正常重排 ====================


def test_rerank_reorders_and_keeps_vector_score(env):
    """按 reranker 给的顺序重排，并把原向量分数留在 vector_score 里（便于对比）。"""
    env["results"] = [_hit(1, 0.90), _hit(2, 0.50), _hit(3, 0.40)]
    env["order"] = [(2, 0.99), (0, 0.70), (1, 0.01)]

    out = vector_service.search(None, _USER, "年假可以休几天", top_k=3, rerank=True)

    assert out["reranked"] is True
    assert [r["chunk_id"] for r in out["results"]] == [3, 1, 2]      # 新顺序
    top = out["results"][0]
    assert top["score"] == 0.99               # score 换成相关性分数
    assert top["vector_score"] == 0.40        # 原向量分数保留
    assert env["rerank"][0]["query"] == "年假可以休几天"
    assert env["rerank"][0]["docs"] == ["候选正文 1", "候选正文 2", "候选正文 3"]


def test_rerank_uses_candidate_k_for_recall_and_trims_to_top_k(env):
    """两段式：Milvus 收到的是 RAG_CANDIDATE_K（多召回），最终只返回 top_k 条。"""
    env["results"] = [_hit(i, 0.5) for i in range(1, 8)]
    env["order"] = [(3, 0.9), (0, 0.8), (1, 0.7), (2, 0.6), (4, 0.5)]

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert env["candidate_k"] == [settings.RAG_CANDIDATE_K]   # 多召回
    assert len(out["results"]) == 2                            # 精排后再截断
    assert env["rerank"][0]["top_n"] == 2


# ==================== 降级与开关 ====================


def test_rerank_failure_degrades_to_vector_order(env):
    """🔴 重排服务挂了**不能把问答搞失败** —— 退回向量分数顺序继续。"""
    env["results"] = [_hit(1, 0.90), _hit(2, 0.50)]
    env["error"] = AppException(ErrorCode.EMBEDDING_ERROR, "重排服务调用失败，请稍后重试")

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert out["reranked"] is False
    assert [r["chunk_id"] for r in out["results"]] == [1, 2]   # 向量顺序，没乱
    assert "vector_score" not in out["results"][0]             # 没重排就不加这个字段


def test_rerank_returns_nothing_also_degrades(env):
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]
    env["order"] = []

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)
    assert out["reranked"] is False
    assert [r["chunk_id"] for r in out["results"]] == [1, 2]


def test_global_switch_off_skips_rerank(env, monkeypatch):
    """总开关关掉 → 即使调用方要求重排也不排，且不做多召回。"""
    monkeypatch.setattr(settings, "RAG_RERANK_ENABLED", False)
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert env["rerank"] == []
    assert out["reranked"] is False
    assert env["candidate_k"] == [2]        # 候选数 = top_k


def test_default_search_does_not_rerank(env):
    """/api/search 的默认行为：纯向量检索（这样才好和重排对比）。"""
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]

    out = vector_service.search(None, _USER, "q", top_k=2)

    assert env["rerank"] == []
    assert out["reranked"] is False
    assert env["candidate_k"] == [2]


def test_single_candidate_skips_rerank(env):
    """只有一条候选时排序无意义，不必白花一次调用。"""
    env["results"] = [_hit(1, 0.9)]

    out = vector_service.search(None, _USER, "q", top_k=5, rerank=True)

    assert env["rerank"] == []
    assert out["reranked"] is False
    assert len(out["results"]) == 1


def test_empty_results_never_calls_rerank(env):
    env["results"] = []
    out = vector_service.search(None, _USER, "q", top_k=5, rerank=True)
    assert env["rerank"] == []
    assert out["results"] == []


def test_empty_scope_response_shape_is_complete(env, monkeypatch):
    """没有可访问的库时，返回结构里也要有 reranked/candidate_k（形状一致）。"""
    monkeypatch.setattr(vector_service, "_resolve_search_scope", lambda db, user, kb_ids: [])
    out = vector_service.search(None, _USER, "q", top_k=5, rerank=True)
    assert out["results"] == []
    assert out["reranked"] is False
    assert out["candidate_k"] == 0


# ==================== reranker 模块本身 ====================


def test_reranker_skips_out_of_range_index(monkeypatch):
    """上游返回越界下标必须跳过 —— 硬用会把分数按到别的文档上。"""
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [{"index": 0, "relevance_score": 0.9},
                                {"index": 9, "relevance_score": 0.8}]}

    monkeypatch.setattr(reranker.httpx, "post", lambda *a, **kw: _Resp())
    got = reranker.rerank("q", ["只有一个文档"], top_n=2)
    assert got == [(0, 0.9)]


def test_reranker_maps_http_error_without_leaking_details(monkeypatch):
    class _Resp:
        status_code = 400
        text = '{"code":20012,"message":"Model does not exist"}'

    monkeypatch.setattr(reranker.httpx, "post", lambda *a, **kw: _Resp())
    with pytest.raises(AppException) as exc:
        reranker.rerank("q", ["d"], top_n=1)

    assert exc.value.code == ErrorCode.EMBEDDING_ERROR
    assert exc.value.message == "重排服务调用失败，请稍后重试"   # 一句话，不带上游原文
    for leak in ("Model does not exist", "20012", "api.siliconflow", "http"):
        assert leak not in exc.value.message


def test_reranker_wraps_network_error(monkeypatch):
    def boom(*a, **kw):
        raise OSError("connection reset")

    monkeypatch.setattr(reranker.httpx, "post", boom)
    with pytest.raises(AppException) as exc:
        reranker.rerank("q", ["d"], top_n=1)
    assert exc.value.code == ErrorCode.EMBEDDING_ERROR
    assert "connection reset" not in exc.value.message


def test_reranker_empty_documents_short_circuits(monkeypatch):
    def should_not_be_called(*a, **kw):
        raise AssertionError("不该发请求")

    monkeypatch.setattr(reranker.httpx, "post", should_not_be_called)
    assert reranker.rerank("q", [], top_n=3) == []


def test_reranker_top_n_is_clamped(monkeypatch):
    """top_n 超过候选数时按候选数收敛，别让上游为难。"""
    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [{"index": 0, "relevance_score": 0.5}]}

    def spy(url, **kw):
        seen["payload"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(reranker.httpx, "post", spy)
    reranker.rerank("q", ["a", "b"], top_n=99)
    assert seen["payload"]["top_n"] == 2


# ==================== 配置与接口透传 ====================


def test_candidate_k_must_cover_top_k_at_startup():
    import pydantic

    from app.config import Settings

    with pytest.raises(pydantic.ValidationError):
        Settings(RAG_TOP_K=10, RAG_CANDIDATE_K=5, RAG_RERANK_ENABLED=True)
    # 关掉重排时不做这个约束（纯向量检索没有"多召回"的概念）
    assert Settings(RAG_TOP_K=10, RAG_CANDIDATE_K=5, RAG_RERANK_ENABLED=False).RAG_TOP_K == 10


def test_chat_response_carries_reranked_flag(client, make_user, monkeypatch):
    """/api/chat 会显式要求重排，并在响应里回报到底排没排。"""
    from app.services import rag_service

    monkeypatch.setattr(
        rag_service.vector_service, "search",
        lambda db, user, query, kb_ids=None, top_k=5, rerank=False: {
            "query": query, "top_k": top_k, "searched_kb_ids": [1],
            "results": [_hit(1, 0.9)], "reranked": True, "candidate_k": 20,
        },
    )
    monkeypatch.setattr(
        rag_service.llm, "chat",
        lambda system, user: {"answer": "答案 [1]", "usage": {"total_tokens": 12},
                              "finish_reason": "stop"},
    )

    admin = make_user("admin")
    r = client.post("/api/chat", json={"query": "q"}, headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["reranked"] is True


# ==================== 可观测性：孤儿与「没重排的原因」 ====================


def test_skipped_orphans_is_passed_through(env):
    """search() 要把 `_hydrate` 报上来的孤儿数**原样透出**，不能吞掉。

    ⚠️ 注意本用例里 `_hydrate` 是被替掉的，所以它验证的是"透传"这一环；
    "真的会数出孤儿"由 tests/test_vectors.py::test_search_skips_orphan_vectors
    用真实的 `_hydrate` 覆盖（跑变异验证时确认过这个分工）。

    为什么这个数字重要：孤儿会白占 top_k 名额、让结果莫名其妙变少。
    实测为排查它花过不少时间（把 limit、索引、一致性、加载状态全查了一遍）。
    """
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]
    env["skipped_orphans"] = 3

    out = vector_service.search(None, _USER, "q", top_k=2)

    assert out["skipped_orphans"] == 3
    assert len(out["results"]) == 2


def test_skip_reason_disabled(env, monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANK_ENABLED", False)
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert out["reranked"] is False
    assert out["rerank_skipped_reason"] == "disabled"


def test_skip_reason_single_candidate(env):
    env["results"] = [_hit(1, 0.9)]

    out = vector_service.search(None, _USER, "q", top_k=5, rerank=True)

    assert out["reranked"] is False
    assert out["rerank_skipped_reason"] == "single_candidate"


def test_skip_reason_failed(env):
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]
    env["error"] = AppException(ErrorCode.EMBEDDING_ERROR, "重排服务调用失败，请稍后重试")

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert out["reranked"] is False
    assert out["rerank_skipped_reason"] == "failed"


def test_skip_reason_no_scope(env, monkeypatch):
    monkeypatch.setattr(vector_service, "_resolve_search_scope", lambda db, user, kb_ids: [])

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert out["rerank_skipped_reason"] == "no_scope"


def test_success_has_no_skip_reason(env):
    env["results"] = [_hit(1, 0.9), _hit(2, 0.5)]
    env["order"] = [(1, 0.9), (0, 0.1)]

    out = vector_service.search(None, _USER, "q", top_k=2, rerank=True)

    assert out["reranked"] is True
    assert out["rerank_skipped_reason"] is None


# ==================== 端到端：编号必须跟着重排走 ====================


def test_prompt_numbering_follows_reranked_order(client, make_user, monkeypatch):
    """🔴 重排换了顺序之后，Prompt 里的 [1] 必须是**重排后的第一条**。

    覆盖 search → _hydrate → build_context → 编号这一整条链。
    这条一旦错位，答案里所有引用编号都会指错文档 ——
    属于"测试全绿、答案却错了、而且没人发现"的那类问题，
    所以值得有一条端到端断言把它钉住。
    """
    from app.services import rag_service, vector_service

    monkeypatch.setattr(vector_service, "_resolve_search_scope", lambda db, user, kb_ids: [1])
    monkeypatch.setattr(
        vector_service.embedding, "embed_query", lambda q: [0.0] * settings.EMBEDDING_DIM
    )
    monkeypatch.setattr(vector_service.vector_store, "search", lambda vec, scope, k: [])
    # 向量顺序：A / B / C
    monkeypatch.setattr(
        vector_service, "_hydrate",
        lambda db, hits: ([_hit(1, 0.9, name="A.txt"), _hit(2, 0.8, name="B.txt"),
                           _hit(3, 0.7, name="C.txt")], 0),
    )
    # 重排把 C（原第三条）顶到第一
    monkeypatch.setattr(
        vector_service.reranker, "rerank",
        lambda q, docs, top_n: [(2, 0.95), (0, 0.40), (1, 0.10)],
    )

    captured: dict = {}

    def fake_chat(system, user):
        captured["user"] = user
        return {"answer": "答 [1]", "usage": {"total_tokens": 9}, "finish_reason": "stop"}

    monkeypatch.setattr(rag_service.llm, "chat", fake_chat)

    admin = make_user("admin")
    body = client.post("/api/chat", json={"query": "问", "top_k": 3},
                       headers=admin["headers"]).json()
    assert body["code"] == 0, body
    assert body["data"]["reranked"] is True

    msg = captured["user"]
    first_block = msg.split("[2]")[0]           # 提示词里的第 1 条资料
    assert "C.txt" in first_block, first_block
    assert "候选正文 3" in first_block, first_block
    assert "A.txt" not in first_block, "编号没跟着重排走"

    # sources 的编号也要对上
    assert [s["index"] for s in body["data"]["sources"]] == [1, 2, 3]
    assert body["data"]["sources"][0]["chunk_id"] == 3
    assert body["data"]["sources"][0]["document_name"] == "C.txt"
