"""进程内固定窗口限流 —— **临时措施**。

⚠️ 只在**单进程**下准确：`uvicorn --workers N`（N > 1）时每个 worker 各算各的账，
   真实额度会被放大 N 倍。所以它是开发 / 单机演示阶段的过渡实现 ——
   等接 Redis 那一步换成跨进程版本（本文件届时整体删除，requirements 里才会第一次出现 redis）。

**为什么现在就要有**：一次 `/api/chat` 会打两个付费上游（rerank + LLM），
而 Phase 8 的 Agent 会**自己决定调用几次工具** —— 没有闸门，一个循环脚本就能把额度烧光。
限流保护的是"钱"，所以它不该等到安全加固阶段才做。

口径：
  · 维度是「登录用户」（user.id），不是 IP —— 接口都要登录，账号级更准，
    也避免 NAT 后面一群人共用一个 IP 互相误伤。
  · 超额抛 `42900`（对应 HTTP 429），与项目「4xxxx = HTTP 状态码 × 100」的分段一致。
"""

import logging
import threading
import time

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

# 每个 kind 的配额：从 settings 现读（不缓存），这样测试 monkeypatch 配置后立即生效
_QUOTA_READERS = {
    "chat": lambda: (settings.RATE_LIMIT_CHAT_PER_MINUTE, 60.0),
    "search": lambda: (settings.RATE_LIMIT_SEARCH_PER_MINUTE, 60.0),
    "vectorize": lambda: (settings.RATE_LIMIT_VECTORIZE_PER_MINUTE, 60.0),
}

# 桶数量超过这个值就顺手清理过期项，避免长跑时内存只增不减
_MAX_BUCKETS = 2000


class FixedWindowLimiter:
    """固定窗口计数器。

    窗口起点用 `time.monotonic()`，不受系统时间被调整的影响。
    固定窗口的固有权衡：窗口边界处最多可能放过 2 倍配额 ——
    对"防脚本烧钱"这个目的完全够用，换来的是实现只有几十行、没有额外状态。
    """

    def __init__(self) -> None:
        # key -> [窗口起点, 计数]
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        """记一次访问。返回 `(是否放行, 建议重试秒数)`。"""
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > _MAX_BUCKETS:
                self._buckets = {
                    k: v for k, v in self._buckets.items() if now - v[0] < window
                }

            start, count = self._buckets.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0

            count += 1
            self._buckets[key] = [start, count]

            if count > limit:
                return False, max(1, int(window - (now - start)) + 1)
            return True, 0

    def check(self, kind: str, user_id: int) -> None:
        """按 kind 取配额并检查，超额直接抛 42900。"""
        if not settings.RATE_LIMIT_ENABLED:
            return

        reader = _QUOTA_READERS.get(kind)
        if reader is None:  # 配错 kind 属于代码错误，直接放行而不是把接口堵死
            logger.warning("未知的限流类型 kind=%s（已跳过限流）", kind)
            return

        limit, window = reader()
        if limit <= 0:  # 配 0 视为"该类型不限流"
            return

        allowed, retry_after = self.allow("%s:%s" % (kind, user_id), limit, window)
        if not allowed:
            logger.warning(
                "触发限流 kind=%s user_id=%s 配额=%s/%ss", kind, user_id, limit, int(window)
            )
            raise AppException(
                ErrorCode.TOO_MANY_REQUESTS, "操作过于频繁，请 %d 秒后再试" % retry_after
            )

    def reset(self) -> None:
        """清空所有计数（测试用：限流器是全局单例，用例之间会互相污染）。"""
        with self._lock:
            self._buckets.clear()


limiter = FixedWindowLimiter()
