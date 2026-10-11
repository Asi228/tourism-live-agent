"""限流的测试（Phase 10 起：Redis 跨进程固定窗口）。

限流保护的不是"安全"，而是**钱**（一次 /api/chat 打两个付费上游），
所以用例重点在"配额真的会拦""不同用户互不干扰""两个 worker 共用一份计数""Redis 挂了怎么办"。

**本文件不连真 Redis**：限流器允许注入客户端，这里注入一个内存替身，
于是用例自洽（Redis VM 不开也能全绿）。"真连 Redis 的交互细节"由
`tests/test_limiter_integration.py` 单独钉，默认跳过。

conftest 里默认把限流关了，本文件自己打开。
"""

import time

import pytest

from app.config import settings
from app.core.exceptions import AppException
from app.core.rate_limit_redis import KEY_PREFIX, RedisFixedWindowLimiter
from app.core.response import ErrorCode


class _FakeRedis:
    """限流用到的那几个命令的最小替身。

    刻意**不引 fakeredis**：这里只需要 incr / expire / scan_iter / delete 四个方法
    （外加一个 TTL 记录，用来断言"确实设了过期"），自己写比多一个依赖便宜。
    """

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self.ttl[key] = ttl
        return True

    def scan_iter(self, match: str = "*"):
        prefix = match.rstrip("*")
        return iter([k for k in list(self.store) if k.startswith(prefix)])

    def delete(self, *keys: str) -> int:
        for k in keys:
            self.store.pop(k, None)
            self.ttl.pop(k, None)
        return len(keys)


class _BrokenRedis:
    """所有命令都失败 —— 模拟 Redis 挂了。"""

    def incr(self, key: str) -> int:
        raise RuntimeError("redis down")

    def expire(self, key: str, ttl: int) -> bool:
        raise RuntimeError("redis down")


def _limiter(fake=None) -> RedisFixedWindowLimiter:
    return RedisFixedWindowLimiter(client=fake if fake is not None else _FakeRedis())


@pytest.fixture
def limit_on(monkeypatch):
    """打开限流，并把**单例自己**的客户端换成内存替身。

    ⚠️ 必须改**单例的属性**，不能 `monkeypatch.setattr(模块, "limiter", 新实例)`。
    原因：`deps.rate_limit()` 里的 `from ... import limiter` 是在**导入路由时**执行的
    （`Depends(rate_limit("chat"))` 是装饰器参数，导入期就求值了），
    那会儿引用已经绑死在原单例上 —— 之后换模块属性对已注册的路由毫无影响。

    这个坑的现场症状很迷惑：**单跑这条用例通过，整文件跑就挂**，
    而且第一条请求就返回 42900 —— 因为端点拿的是**真单例**，计数落在真 Redis 上，
    还带着上一次运行残留的计数。
    """
    from app.core import rate_limit_redis

    singleton = rate_limit_redis.limiter
    monkeypatch.setattr(singleton, "_client", _FakeRedis())
    monkeypatch.setattr(singleton, "_down_until", 0.0)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    return singleton


# ==================== 限流器本身 ====================


def test_fixed_window_counts_and_blocks():
    lim = _limiter()
    for _ in range(3):
        assert lim.allow("k", limit=3, window=60)[0] is True
    allowed, retry = lim.allow("k", limit=3, window=60)
    assert allowed is False
    assert retry >= 1            # 给出建议重试秒数


def test_fixed_window_is_per_key():
    lim = _limiter()
    for _ in range(2):
        lim.allow("a", limit=2, window=60)
    assert lim.allow("a", limit=2, window=60)[0] is False
    assert lim.allow("b", limit=2, window=60)[0] is True   # 另一个 key 不受影响


def test_key_carries_window_bucket_and_ttl():
    """窗口序号写进 key，所以**不需要任何"重置"逻辑**：窗口一过就是新 key。

    同时断言 TTL 真的设了（比窗口多 1 秒留边界余量）——
    不设 TTL 的话这些键会在 Redis 里越堆越多。
    """
    fake = _FakeRedis()
    _limiter(fake).allow("chat:1", limit=1, window=60)

    (key,) = fake.store
    assert key.startswith(f"{KEY_PREFIX}chat:1:")
    assert key.rsplit(":", 1)[1].isdigit(), "key 里没带窗口序号"
    assert fake.ttl[key] == 61


def test_window_bucket_comes_from_wall_clock():
    """窗口序号必须由**墙上时钟**算出来 —— 这条挡住"顺手统一时钟"。

    `time.monotonic()` 的基线是"**各进程各自**的开机时刻"：多 worker 下每个进程算出的
    窗口序号都不同 → key 不同 → 计数各算各的 → **跨进程限流直接失效**，
    而那正是这个文件存在的全部理由。

    光写注释没用（内存替身的用例照样全绿、真 Redis 的集成用例默认跳过），
    所以这里直接比对 key 里的序号与 `time.time()` 算出来的是否一致。
    """

    fake = _FakeRedis()
    _limiter(fake).allow("chat:1", limit=1, window=60)

    (key,) = fake.store
    bucket = int(key.rsplit(":", 1)[1])
    expected = int(time.time()) // 60

    assert abs(bucket - expected) <= 1, (
        "窗口序号不是墙上时钟算的（拿到 %s，按 time.time() 应为 %s）—— "
        "换成 monotonic 之后多 worker 会各算各的桶，跨进程限流就失效了" % (bucket, expected)
    )
    # 量级兜底：monotonic 是"开机秒数"，开机多久都不可能到这个量级
    assert bucket > 10 ** 6, "序号量级不像 Unix 时间戳（monotonic 会小好几个数量级）"


def test_two_workers_share_one_counter():
    """**跨进程**才是这次改造的全部目的。

    两个独立实例 = 两个 worker。进程内版本在这里会各算各的（额度放大 N 倍），
    Redis 版本必须共享同一份计数。
    """
    shared = _FakeRedis()                      # 一台"Redis"
    w1 = RedisFixedWindowLimiter(client=shared)
    w2 = RedisFixedWindowLimiter(client=shared)

    assert w1.allow("chat:7", limit=2, window=60)[0] is True
    assert w2.allow("chat:7", limit=2, window=60)[0] is True, "第二个 worker 看不见第一个的计数"
    assert w1.allow("chat:7", limit=2, window=60)[0] is False, "配额被两个 worker 各自放大了"
    assert w2.allow("chat:7", limit=2, window=60)[0] is False


# ==================== Redis 不可用时的降级 ====================


def test_redis_down_degrades_to_allow():
    """Redis 挂了**放行**，而不是把问答整个打死 —— 这是刻意选的降级方向。

    （代价是这段时间额度失控，所以 /health 会把 Redis 单独报出来。）
    """
    lim = _limiter(_BrokenRedis())
    for _ in range(50):
        assert lim.allow("k", limit=1, window=60)[0] is True


def test_redis_down_enters_cooldown():
    """失败后 30 秒内不再重试。

    否则每个请求都要等一次 2 秒连接超时，限流器就从"保护成本"变成"放慢所有请求"。
    """
    calls = {"n": 0}

    class _Counting(_BrokenRedis):
        def incr(self, key: str) -> int:
            calls["n"] += 1
            raise RuntimeError("down")

    lim = _limiter(_Counting())
    for _ in range(6):
        lim.allow("k", limit=1, window=60)

    assert calls["n"] == 1, "冷却期内不该反复去连 Redis"


def test_reset_only_touches_our_prefix():
    """`reset()` 只删自己写的前缀键 —— 这台 Redis 是共用的，不能把别人的键一起清了。"""
    fake = _FakeRedis()
    lim = _limiter(fake)
    lim.allow("chat:1", limit=5, window=60)
    fake.store["someone-elses-key"] = 123

    lim.reset()

    assert "someone-elses-key" in fake.store
    assert not [k for k in fake.store if k.startswith(KEY_PREFIX)]


# ==================== check() 的分支 ====================


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


def test_switch_off_means_no_limit(limit_on, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_CHAT_PER_MINUTE", 1)
    for _ in range(50):
        limit_on.check("chat", user_id=1)       # 不该抛


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
        lambda system, user, history=None: {"answer": "答 [1]", "usage": {"total_tokens": 5},
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
