"""agent_logs 的数据访问。

刻意只提供 `create`：写入是本阶段唯一真实需要的操作。
（读的那一侧等真有"查看执行历史"的接口时再加 —— 先写个没人调的查询函数只会变成死代码。）
"""

import logging

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
