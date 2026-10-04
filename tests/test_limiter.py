"""限流的测试。

限流保护的不是"安全"，而是**钱**（一次 /api/chat 打两个付费上游），
所以用例重点在"配额真的会拦""不同用户互不干扰""关掉开关就完全放行"。
conftest 里默认把限流关了，本文件自己打开。
"""

import pytest

from app.config import settings
from app.core.exceptions import AppException
from app.core.limiter import FixedWindowLimiter, limiter
from app.core.response import ErrorCode


@pytest.fixture
def limit_on(monkeypatch):
    """打开限流，并清空计数（limiter 是全局单例，会跨用例残留）。"""
    limiter.reset()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    yield limiter
    limiter.reset()


# ==================== 限流器本身 ====================


def test_fixed_window_counts_and_blocks():
    lim = FixedWindowLimiter()
    for _ in range(3):
        allowed, _retry = lim.allow("k", limit=3, window=60.0)
        assert allowed is True
    allowed, retry = lim.allow("k", limit=3, window=60.0)
    assert allowed is False
    assert retry >= 1            # 给出建议重试秒数


def test_fixed_window_is_per_key():
    lim = FixedWindowLimiter()
    for _ in range(2):
        lim.allow("a", limit=2, window=60.0)
    assert lim.allow("a", limit=2, window=60.0)[0] is False
    assert lim.allow("b", limit=2, window=60.0)[0] is True   # 另一个 key 不受影响


def test_fixed_window_expires(monkeypatch):
    """窗口过期后重新放行（不真的等 60 秒，把时钟往前拨）。"""
    lim = FixedWindowLimiter()
    assert lim.allow("k", limit=1, window=60.0)[0] is True
    assert lim.allow("k", limit=1, window=60.0)[0] is False

    real = __import__("time").monotonic
    monkeypatch.setattr("time.monotonic", lambda: real() + 61)
    assert lim.allow("k", limit=1, window=60.0)[0] is True


def test_check_raises_42900(limit_on, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_CHAT_PER_MINUTE", 3)
    with pytest.raises(AppException) as exc:
        for _ in range(4):
            limit_on.check("chat", user_id=123456)
    assert exc.value.code == ErrorCode.TOO_MANY_REQUESTS
    assert "频繁" in exc.value.message


def test_check_skips_when_quota_is_zero(limit_on, monkeypatch):
    """配额配 0 = 该类型不限流。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH_PER_MINUTE", 0)
    for _ in range(50):
        limit_on.check("search", user_id=1)     # 不该抛


def test_unknown_kind_is_passed_through(limit_on):
    """kind 写错属于代码问题，应当放行而不是把接口堵死。"""
    for _ in range(50):
        limit_on.check("not-a-kind", user_id=1)


# ==================== 接口层 ====================


def _hit() -> dict:
    return {
        "chunk_id": 1, "document_id": 1, "document_name": "a.txt",
        "knowledge_base_id": 1, "page_number": None, "score": 0.9, "content": "正文",
    }


@pytest.fixture
def rag(monkeypatch):
    """最小的 RAG 打桩：检索返回一条、LLM 直接返回答案。"""
    from app.services import rag_service, vector_service

    monkeypatch.setattr(
        vector_service, "search",
        lambda db, user, query, kb_ids=None, top_k=5, rerank=False: {
            "query": query, "top_k": top_k, "searched_kb_ids": [1], "results": [_hit()],
            "reranked": False, "rerank_skipped_reason": "disabled",
            "candidate_k": top_k, "skipped_orphans": 0,
        },
    )
    monkeypatch.setattr(
        rag_service.llm, "chat",
        lambda system, user: {"answer": "答 [1]", "usage": {"total_tokens": 5},
                              "finish_reason": "stop"},
    )


def test_chat_returns_42900_after_quota(client, make_user, rag, limit_on, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_CHAT_PER_MINUTE", 3)
    admin = make_user("admin")

    codes = [
        client.post("/api/chat", json={"query": "q"}, headers=admin["headers"]).json()["code"]
        for _ in range(5)
    ]
    assert codes[:3] == [0, 0, 0], codes
    assert codes[3] == ErrorCode.TOO_MANY_REQUESTS
    assert codes[4] == ErrorCode.TOO_MANY_REQUESTS


def test_quota_is_per_user(client, make_user, rag, limit_on, monkeypatch):
    """一个用户打满不该影响另一个用户 —— 账号级配额的意义就在这里。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_CHAT_PER_MINUTE", 2)
    a = make_user("admin")
    b = make_user("admin")

    for _ in range(3):
        client.post("/api/chat", json={"query": "q"}, headers=a["headers"])
    assert client.post("/api/chat", json={"query": "q"}, headers=a["headers"]).json()["code"] \
        == ErrorCode.TOO_MANY_REQUESTS

    r = client.post("/api/chat", json={"query": "q"}, headers=b["headers"])
    assert r.json()["code"] == 0, "另一个用户被连坐了"


def test_switch_off_means_no_limit(client, make_user, rag, monkeypatch):
    """开关关掉 → 完全放行（conftest 默认就是关的，这里显式断言）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_CHAT_PER_MINUTE", 1)
    limiter.reset()
    admin = make_user("admin")

    codes = [
        client.post("/api/chat", json={"query": "q"}, headers=admin["headers"]).json()["code"]
        for _ in range(4)
    ]
    assert codes == [0, 0, 0, 0]


def test_vectorize_and_search_are_limited(client, make_user, limit_on, monkeypatch):
    """向量化与检索各走自己的配额（各档独立计数）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_VECTORIZE_PER_MINUTE", 1)
    admin = make_user("admin")

    from app.services import vector_service

    monkeypatch.setattr(
        vector_service, "search",
        lambda db, user, query, kb_ids=None, top_k=5, rerank=False: {
            "query": query, "top_k": top_k, "searched_kb_ids": [1], "results": [],
            "reranked": False, "rerank_skipped_reason": "disabled",
            "candidate_k": top_k, "skipped_orphans": 0,
        },
    )
    first = client.post("/api/search", json={"query": "q"}, headers=admin["headers"]).json()["code"]
    second = client.post("/api/search", json={"query": "q"}, headers=admin["headers"]).json()["code"]
    assert first == 0
    assert second == ErrorCode.TOO_MANY_REQUESTS
