"""agent_logs 的数据访问。

只有两个函数，都是**写入生命周期**上的：`create` 写、`prune_older_than` 清。
（读的那一侧等真有"查看执行历史"的接口时再加 —— 先写个没人调的查询函数只会变成死代码。）
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.agent_log import AgentLog

logger = logging.getLogger(__name__)


def create(
    db: Session,
    *,
    user_id: int,
    query: str,
    answer: str | None,
    hit: bool,
    source_count: int,
    iterations: int,
    steps: list[dict] | None,
    total_tokens: int,
    elapsed_ms: int,
    error_msg: str | None = None,
) -> AgentLog | None:
    """写一条执行记录。

    **只 flush、不 commit**：事务边界归 service 层 —— 这是全项目一致的约定
    （其余 7 个 repository 都不结束事务），提交由 `agent_service._write_log` 决定。
    在 repo 里提前 commit 会截断将来那个"messages + agent_logs 同事务"的场景，
    而且破坏点藏在一个看起来人畜无害的函数里。

    **失败返回 None 而不抛异常**：这是审计数据，不是业务结果 ——
    写日志失败不该让用户拿不到已经生成好的答案。
    """
    try:
        row = AgentLog(
            user_id=user_id,
            query=query[:1000],
            answer=answer,
            hit=hit,
            source_count=source_count,
            iterations=iterations,
            steps=steps,
            total_tokens=total_tokens,
            elapsed_ms=elapsed_ms,
            error_msg=(error_msg or None) and error_msg[:500],
        )
        db.add(row)
        db.flush()
        return row
    except Exception as exc:  # noqa: BLE001
        # flush 失败会把 session 留在"必须 rollback 才能继续用"的状态上，
        # 所以这里必须清毒；清理后返回 None，调用方据此跳过 commit。
        db.rollback()
        logger.warning("写入 agent_logs 失败（不影响本次回答）：%s", str(exc)[:200])
        return None


def prune_older_than(db: Session, days: int) -> int:
    """删掉 N 天前的执行记录，返回删除条数；`days <= 0` 表示不清理。

    表结构上 `created_at` 有索引，所以这条范围删除是走索引的。

    **不 commit**：口径与 `create` 一致 —— 事务边界归 service 层。

    ⚠️ 失败时**只记 warning、不 rollback**。这一点和 `create` 刻意不同：
    `create` 失败时 session 已经被搞脏、必须清毒，而它此刻还没来得及写业务数据；
    而这里（留存的清理）跑在"这一轮问答已经写了一半"的事务里，
    一旦 rollback 就会把**本轮刚写好的审计与对话一起丢掉** —— 那比"少清几条旧日志"严重得多。
    所以宁可让它在最终 commit 时按普通 DB 错误暴露出来。
    """
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    try:
        result = db.execute(delete(AgentLog).where(AgentLog.created_at < cutoff))
        db.flush()
        return result.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理过期 agent_logs 失败（不影响本次回答）：%s", str(exc)[:200])
        return 0
