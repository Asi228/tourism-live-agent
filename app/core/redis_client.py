"""Redis 客户端（惰性单例）。

谁在用：限流（`app/core/rate_limit_redis.py`）与健康检查（`app/api/health.py`）。
两处都只关心"通不通 / 能不能计数"，所以这里就把连接收成一份，别各建各的。

**超时写死 2 秒、不做成配置项**：它的唯一作用是在"Redis 挂了"时兜住响应时间。
为此多一个配置项，就要多维护 `.env` 三份（config / .env / .env.example），不划算。
"""

import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# 连接 + 读写超时。Redis 不可用时，接口最多为它多等这么久 —— 所以取值要小。
TIMEOUT_SECONDS = 2.0

_client: "redis.Redis | None" = None


def get_client() -> "redis.Redis":
    """惰性建连接池。**只建不探活** —— 探活交给 `ping()`，由调用方决定怎么处理失败。"""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=TIMEOUT_SECONDS,
            socket_timeout=TIMEOUT_SECONDS,
            decode_responses=True,
        )
        # 内网地址只进日志、不进响应体（项目既有口径）
        logger.info(
            "Redis 客户端已创建 host=%s port=%s db=%s",
            settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB,
        )
    return _client


def ping() -> bool:
    """探活，**不抛异常**。

    健康检查要的答案是"通不通"这一个布尔值；把异常抛出去只会让 /health 自己变成 500。
    真因（拒绝连接 / 认证失败）完整进日志。
    """
    try:
        get_client().ping()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连通性检查失败: %s", str(exc)[:200])
        return False
