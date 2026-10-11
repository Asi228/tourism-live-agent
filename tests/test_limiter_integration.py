"""Phase 10 集成测试：**真连 Redis**，钉住限流器与真实客户端的交互细节。

默认跳过。要跑：

    PowerShell:  $env:RUN_REDIS_INTEGRATION="1"; python -m pytest tests/test_limiter_integration.py -v

为什么单开一个文件、默认不跑：理由与 `tests/test_vectors_integration.py` 完全一致 ——
它依赖外部服务（VM 上的 Redis），塞进主测试集会让"Redis 没开机"变成一片红，
别人 clone 下来第一件事就是测试全挂，那是很差的第一印象。

它盯的是**内存替身盯不到的东西**：
  1. redis-py 的真方法签名与行为（incr / expire / scan_iter / ttl / delete）——
     替身是我自己写的，方法名写错了照样全绿；
  2. TTL 是不是真的落在服务端上（替身只能"记下来"，证明不了 Redis 接受它）；
  3. 两个独立实例读的是**同一份**计数 —— 这正是这次改造全部的意图。

收尾只删本限流器写的前缀键：这台 Redis 是共用的，不能连别人的键一起清。
"""

import os

import pytest

from app.core.rate_limit_redis import KEY_PREFIX, RedisFixedWindowLimiter

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_INTEGRATION") != "1",
    reason="需要真实 Redis；设 RUN_REDIS_INTEGRATION=1 才跑",
)


@pytest.fixture
def lim():
    instance = RedisFixedWindowLimiter()
    instance.reset()
    yield instance
    instance.reset()


def test_real_redis_counts_and_sets_ttl(lim):
    for _ in range(3):
        assert lim.allow("probe", limit=3, window=60)[0] is True
    assert lim.allow("probe", limit=3, window=60)[0] is False

    from app.core.redis_client import get_client

    client = get_client()
    keys = list(client.scan_iter(match="%sprobe:*" % KEY_PREFIX))
    assert keys, "计数没有落到 Redis 上"
    assert client.ttl(keys[0]) > 0, "没设过期时间，这些键会一直堆积"


def test_two_instances_share_counter_on_real_redis():
    """跨进程的实质：两个独立实例（各一条连接）读到的必须是同一份计数。"""
    first, second = RedisFixedWindowLimiter(), RedisFixedWindowLimiter()
    first.reset()
    try:
        assert first.allow("shared", limit=2, window=60)[0] is True
        assert second.allow("shared", limit=2, window=60)[0] is True
        assert first.allow("shared", limit=2, window=60)[0] is False, "配额被按实例各算了一份"
    finally:
        first.reset()
