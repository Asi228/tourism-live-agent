"""健康检查的测试（Phase 10 补齐）。

盯的是：三样组件的通断**都能被看见**。

在此之前 `/health` 只探 MySQL —— README 写着「MySQL / Milvus / Redis」，
而 Milvus 挂了、Redis 挂了，`/health` 照样返回 code=0。那是最坏的一种健康检查：
它给出的是"健康"这个**错误答案**，运维照着它排查会先排除掉真正的问题。

另外还盯着两条"探活别把自己拖死"的性质：
  · Milvus 探活必须用**自己的 2 秒超时**，不能借那个 10 秒的共享客户端；
  · 三探必须**并发**（否则最坏耗时是三者之和），且有结果缓存（接口不需要登录）。

三样都不真连（全部 monkeypatch）：这里测的是"拼装与上报口径"，不是连通性本身。
"""

import time

import pytest

from app.api import health as health_mod
from app.core.response import ErrorCode


def _patch(monkeypatch, mysql=True, milvus=True, redis=True):
    monkeypatch.setattr(health_mod, "_check_mysql", lambda: mysql)
    monkeypatch.setattr(health_mod, "_check_milvus", lambda: milvus)
    monkeypatch.setattr(health_mod.redis_client, "ping", lambda: redis)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """结果缓存是**模块级**状态，会跨用例残留 —— 必须逐用例清掉。

    （不清的话，"上一个用例造出来的 payload"会被下一个用例读到，
      症状是"单跑通过、整文件跑挂"那类难查的串味。）
    """
    health_mod._reset_cache()
    yield
    health_mod._reset_cache()


def test_all_ok(client, monkeypatch):
    _patch(monkeypatch)

    body = client.get("/api/health").json()
    assert body["code"] == 0, body
    assert body["data"] == {"api": "ok", "mysql": "ok", "milvus": "ok", "redis": "ok"}


def test_redis_down_is_reported(client, monkeypatch):
    """Redis 只影响限流，但**必须报出来** —— 它挂着的这段时间额度是失控的。"""
    _patch(monkeypatch, redis=False)

    body = client.get("/api/health").json()
    assert body["code"] == ErrorCode.REDIS_ERROR, body
    assert body["data"]["redis"] == "fail"
    assert body["data"]["mysql"] == "ok", "一个组件挂了不该把别的也说成挂"


def test_mysql_takes_priority_over_upper_layers(client, monkeypatch):
    """基础存储先报 —— 别让上层依赖的错误盖住根因。"""
    _patch(monkeypatch, mysql=False, milvus=False, redis=False)

    assert client.get("/api/health").json()["code"] == ErrorCode.DB_ERROR


def test_error_body_does_not_leak_internal_address(client, monkeypatch):
    """失败时只给一句话，不带主机名 / 端口 —— 内网拓扑不进响应体。"""
    _patch(monkeypatch, milvus=False)

    resp = client.get("/api/health")
    assert resp.json()["code"] == ErrorCode.MILVUS_ERROR
    assert "192.168" not in resp.text
    assert "19530" not in resp.text


# ==================== 探活不能把自己拖死 ====================


def test_milvus_probe_uses_its_own_short_timeout(monkeypatch):
    """Milvus 探活必须走**独立短超时**，不能碰那个 10 秒的共享客户端。

    这是最容易被写回去的地方：`vector_store.get_client()` 就在旁边、看着更方便，
    但它的超时是 MILVUS_TIMEOUT（默认 10s）且**失败不缓存** —— VM 一关机，
    每次探活都要等满 10 秒，探活脚本先超时，"Milvus 挂了"就被误报成"/health 无响应"。
    """
    from app.rag import vector_store

    seen = {}

    def fake_probe(timeout_seconds):
        seen["timeout"] = timeout_seconds

    monkeypatch.setattr(vector_store, "probe", fake_probe)
    monkeypatch.setattr(
        vector_store, "get_client",
        lambda: pytest.fail("探活用了共享客户端（10 秒超时那条路）"),
    )

    assert health_mod._check_milvus() is True
    assert seen["timeout"] == health_mod.PROBE_TIMEOUT_SECONDS
    assert health_mod.PROBE_TIMEOUT_SECONDS <= 2.0, "探活超时回到了 10 秒量级"


def test_slow_probes_do_not_add_up(client, monkeypatch):
    """探活慢的时候，总耗时不该是**三者相加**。

    只做"短超时"是不够的：三个 2 秒串起来还是 4 秒，探活脚本照样先超时。
    所以这里卡 3 秒 —— 并发实现约 2 秒过，串行实现约 4 秒挂。
    """
    def slow_ok():
        time.sleep(2.0)
        return True

    monkeypatch.setattr(health_mod, "_check_mysql", lambda: True)
    monkeypatch.setattr(health_mod, "_check_milvus", slow_ok)
    monkeypatch.setattr(health_mod.redis_client, "ping", slow_ok)

    started = time.monotonic()
    body = client.get("/api/health").json()
    elapsed = time.monotonic() - started

    assert body["data"] == {"api": "ok", "mysql": "ok", "milvus": "ok", "redis": "ok"}
    assert elapsed >= 1.9, "没真的走到慢探测，这条用例失去意义"
    assert elapsed < 3.0, "三个探测像是串行跑的"


def test_result_is_cached_within_ttl(client, monkeypatch):
    """3 秒内复用结果。

    这个接口**不需要登录**，不缓存就等于给任何人一个"每个请求占线程数秒"的放大器。
    """
    calls = {"n": 0}

    def counting_mysql():
        calls["n"] += 1
        return True

    monkeypatch.setattr(health_mod, "_check_mysql", counting_mysql)
    monkeypatch.setattr(health_mod, "_check_milvus", lambda: True)
    monkeypatch.setattr(health_mod.redis_client, "ping", lambda: True)

    for _ in range(5):
        assert client.get("/api/health").json()["code"] == 0

    assert calls["n"] == 1, "缓存没生效，每次请求都真去探了"
