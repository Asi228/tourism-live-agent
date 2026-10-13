"""健康检查。

探 **MySQL / Milvus / Redis** 三样，逐项报 `ok/fail`。

三条都是踩过坑之后才定下来的：

1. **三样都探**。早先只探 MySQL，却写着"MySQL / Milvus / Redis" —— 那是最坏的
   一种健康检查：它给出"健康"这个**错误答案**，照着它排查会先排除掉真正的问题。
2. **探活要短超时**。Milvus 的共享客户端超时是 10 秒且失败不缓存，
   直接拿它探活的话，VM 一关机每次请求就要等满 10 秒 —— 探活脚本先超时，
   于是"Milvus 挂了"被误报成"/health 无响应"。探活只回答"通不通"。
   （用 `vector_store.probe()`，一次性客户端、2 秒。）
3. **三探并发 + 结果缓存**。串行最坏耗时是三者之和；而且这个接口**不需要登录**，
   没有缓存就等于给了任何人一个"每次占用线程数秒"的放大器。详见下面两个常量。
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from sqlalchemy import text

from app.core import redis_client
from app.core.response import ApiResponse, ErrorCode, error, success
from app.db.session import engine
from app.schemas.system import HealthOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# 探活动作的超时。刻意与 `redis_client.TIMEOUT_SECONDS` 取同一个量级：
# 两个都是"探测"，一个 2 秒、另一个 10 秒本身就是 bug 的形状。
PROBE_TIMEOUT_SECONDS = 2.0

# 结果缓存时长。
# `/health` 不需要登录，而每次要打三个外部依赖 —— 不缓存的话，任何人都能靠它占满线程池
# （每个请求最多占 PROBE_TIMEOUT_SECONDS）。3 秒是"挡得住轮询"与"故障能及时发现"之间的折中。
# 代价：最多 3 秒内可能返回略旧的结果，对探活用途可以接受。
PROBE_CACHE_SECONDS = 3.0

_cache: "tuple[float, dict] | None" = None


def _check_mysql() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        # 真因（Access denied / can't connect）完整记日志，脱敏由日志层负责
        logger.warning("MySQL 连通性检查失败: %s", e)
        return False


def _check_milvus() -> bool:
    try:
        from app.rag import vector_store

        vector_store.probe(PROBE_TIMEOUT_SECONDS)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Milvus 连通性检查失败: %s", str(e)[:200])
        return False


def _probe_all() -> dict:
    """三探**并发**跑，返回逐项状态。

    串行的话最坏耗时是三者之和；并发之后上界是其中最慢的那一个（约 PROBE_TIMEOUT_SECONDS）。
    端点本身是同步 `def`（FastAPI 把它放线程池里跑），这里再开三个线程是安全的：
    三件事都是阻塞 IO、互不共享可变状态。
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        db_future = pool.submit(_check_mysql)
        milvus_future = pool.submit(_check_milvus)
        redis_future = pool.submit(redis_client.ping)

        return {
            "api": "ok",
            "mysql": "ok" if db_future.result() else "fail",
            "milvus": "ok" if milvus_future.result() else "fail",
            "redis": "ok" if redis_future.result() else "fail",
        }


def _reset_cache() -> None:
    """清掉结果缓存（测试用：缓存是模块级状态，会跨用例残留）。"""
    global _cache
    _cache = None


@router.get("/health", response_model=ApiResponse[HealthOut], summary="健康检查")
def health():
    """健康检查：MySQL / Milvus / Redis 各探一次（3 秒内复用结果）。

    HTTP 状态码仍统一 200（项目约定），但有不健康的组件时 code 会返回对应的错误码，
    监控 / 探活脚本靠 body 里的 code 就能判定，不会把故障误判成健康。
    优先级 MySQL → Milvus → Redis：**基础存储先报**，别让上层依赖的错误盖住根因。

    三样的名字都直接写在 body 里，这样"到底哪一个挂了"一眼可见，不用去翻日志。
    """
    global _cache

    now = time.monotonic()
    if _cache is not None and now - _cache[0] < PROBE_CACHE_SECONDS:
        payload = _cache[1]
    else:
        payload = _probe_all()
        _cache = (now, payload)

    if payload["mysql"] != "ok":
        # 对外只给一句话，不把主机名、用户名等内部信息带进响应体
        return error(ErrorCode.DB_ERROR, "MySQL 不可用", payload)
    if payload["milvus"] != "ok":
        return error(ErrorCode.MILVUS_ERROR, "向量服务不可用", payload)
    if payload["redis"] != "ok":
        return error(ErrorCode.REDIS_ERROR, "Redis 不可用", payload)
    return success(payload)
