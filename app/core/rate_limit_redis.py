"""跨进程固定窗口限流（基于 Redis）。

Phase 10 用它替换了原先的进程内实现（原 `app/core/limiter.py` **已整体删除**）。
原因只有一个：原实现只在单进程下准确 —— `uvicorn --workers N`（N>1）时每个 worker
各算各的账，真实额度被放大 N 倍。而限流保护的是**钱**（一次 `/api/chat` 打
rerank + LLM 两个付费上游，Agent 还会自己决定调几次工具），额度被放大就等于没有闸门。

口径（与旧实现保持一致，只把存储换成 Redis）：
  · 维度是「登录用户」（`user.id`），不是 IP —— 接口都要登录，账号级更准，
    也避免 NAT 后面一群人共用一个 IP 互相误伤；
  · 每 kind 独立配额、独立计数，当前都是 60 秒固定窗口；
  · 超额抛 `42900`（对应 HTTP 429），与项目「4xxxx = HTTP 状态码 × 100」的分段一致。

⚠️ **Redis 不可用时一律放行**（放行 + warning，并在 30 秒内不再重试）。
   理由：限流保护的是成本，不是安全边界；Redis 抖动不该把问答整个打死 ——
   这与项目"重排失败必须降级"是同一个取向。
   但代价必须说清楚：**Redis 长期不可用的这段时间，额度是失控的**。
   所以 `GET /api/health` 会把 Redis 单独报出来，别让它悄悄发生。
"""

import logging
import time

from app.config import settings
from app.core.exceptions import AppException
from app.core.redis_client import get_client
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

# 每个 kind 的配额：从 settings **现读**（不缓存），这样测试 monkeypatch 配置后立即生效
_QUOTA_READERS = {
    "chat": lambda: (settings.RATE_LIMIT_CHAT_PER_MINUTE, 60),
    "search": lambda: (settings.RATE_LIMIT_SEARCH_PER_MINUTE, 60),
    "vectorize": lambda: (settings.RATE_LIMIT_VECTORIZE_PER_MINUTE, 60),
}

# 键前缀：既是命名空间，也是清理时的唯一依据（`reset()` 只动这个前缀，不碰 Redis 里的别的东西）
KEY_PREFIX = "ratelimit:"

# Redis 挂掉后的冷却时间。没有它的话，每个请求都要等一次 2 秒连接超时才放行 ——
# 限流器会从"保护成本"变成"放慢所有请求"。
_DOWN_COOLDOWN_SECONDS = 30.0


class RedisFixedWindowLimiter:
    """固定窗口计数器（Redis 版）。

    窗口序号直接写进 key：`ratelimit:{kind}:{user_id}:{窗口序号}`。
    窗口一过，新 key 自然从 1 开始计数，**不需要任何"重置"逻辑**。

    固定窗口的固有权衡没变：窗口边界处最多可能放过 2 倍配额。
    对"防脚本烧钱"这个目的完全够用，换来的是实现只有几十行、没有额外状态。
    """

    def __init__(self, client=None) -> None:
        # client 可注入：测试用内存替身，就不必依赖一台真的 Redis 才能跑用例。
        self._client = client
        self._down_until = 0.0

    # ---------- 内部 ----------

    def _conn(self):
        if self._client is None:
            self._client = get_client()
        return self._client

    def _degrade(self, exc: Exception) -> None:
        """Redis 出错 → 记录冷却并放行（见文件头）。"""
        self._down_until = time.monotonic() + _DOWN_COOLDOWN_SECONDS
        logger.warning(
            "限流 Redis 不可用，接下来 %ss 内直接放行: %s",
            int(_DOWN_COOLDOWN_SECONDS), str(exc)[:200],
        )

    # ---------- 对外 ----------

    def allow(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """记一次访问，返回 `(是否放行, 建议重试秒数)`。Redis 不可用 → 放行。"""
        # 冷却判断用 monotonic：单调时钟不会因为系统时间被调整而跳变，
        # 而且它只在本进程内比较（_down_until 是本进程自己写的），没有跨进程一致性要求。
        if time.monotonic() < self._down_until:
            return True, 0

        # ⚠️ 窗口序号必须用**墙上时钟** time.time()，这里不能用 monotonic —— 这是硬约束：
        #    monotonic 的基线是"**各进程各自**的开机时刻"，多 worker 下每个进程算出的
        #    窗口序号都不同 → key 不同 → 计数各算各的 → **跨进程限流直接失效**，
        #    而那正是这个文件存在的全部理由。
        #    （反过来，上面那个冷却只在进程内比较，所以用 monotonic 才对。
        #     同一个函数里两个时钟，是刻意分工，别"顺手统一"。）
        now = int(time.time())
        bucket = now // window
        redis_key = "%s%s:%s" % (KEY_PREFIX, key, bucket)
        try:
            client = self._conn()
            count = client.incr(redis_key)
            # 每次都刷 TTL：万一某次的 EXPIRE 没发出去（连接在两条命令之间断了），
            # 下一次请求也会补上 —— 不会留下一个"永不过期"的计数把人永久锁死。
            client.expire(redis_key, window + 1)
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)
            return True, 0

        if count > limit:
            return False, max(1, window - (now % window))
        return True, 0

    def check(self, kind: str, user_id: int) -> None:
        """按 kind 取配额并检查，超额直接抛 42900。"""
        if not settings.RATE_LIMIT_ENABLED:
            return

        reader = _QUOTA_READERS.get(kind)
        if reader is None:  # 配错 kind 属于代码错误，放行而不是把接口堵死
            logger.warning("未知的限流类型 kind=%s（已跳过限流）", kind)
            return

        limit, window = reader()
        if limit <= 0:  # 配 0 视为"该类型不限流"
            return

        allowed, retry_after = self.allow("%s:%s" % (kind, user_id), limit, window)
        if not allowed:
            logger.warning(
                "触发限流 kind=%s user_id=%s 配额=%s/%ss", kind, user_id, limit, window
            )
            raise AppException(
                ErrorCode.TOO_MANY_REQUESTS, "操作过于频繁，请 %d 秒后再试" % retry_after
            )

    def reset(self) -> None:
        """删掉本限流器写进 Redis 的键（测试清场 / 运维手工重置用）。

        只按 `KEY_PREFIX` 前缀扫，不动 Redis 里的其它键 —— 这台 Redis 是共用的。
        """
        client = self._conn()
        keys = list(client.scan_iter(match="%s*" % KEY_PREFIX))
        if keys:
            client.delete(*keys)
        self._down_until = 0.0


limiter = RedisFixedWindowLimiter()
