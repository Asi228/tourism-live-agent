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
        # ⚠️ **必须放在 SAVEPOINT 里**（与 `prune_older_than` 同一写法、同一理由）。
        # 这条 insert 跑在"本轮问答已经写了一半"的事务里：会话行**已经 flush 过**、
        # 消息还没写。审计自身出错时若回滚整个 session，那个刚 flush 出来的会话行
        # 会跟着消失（它的 Python 对象还在、`conv.id` 也还有值），于是紧随其后的
        # `append_turn` 拿一个已经不存在的 id 去插消息 —— SQLite 默认不开外键，
        # 会静默留下孤儿消息；MySQL 上 `messages_ibfk_1` 会直接报 1452。
        # 保存点把"少一条审计"和"这个会话到底存不存在"彻底切开。
        #
        # 这里可以放心 flush：此刻 session 里 pending 的只有 AuditLog 这一行
        # （会话早已 flush 过、消息还没 add），不会把别的行卷进保存点。
        # ⚠️ 但这是个**隐式前提** —— 将来若有人把写审计挪到 add 消息之后，
        # 就会重演 `prune_older_than` 注释里担心的那种"整批 pending 被写进保存点"。
        with db.begin_nested():
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
        # 保存点已经自己回滚了 **这一小段**，session 仍然是干净的、可以接着写对话历史 ——
        # 这与"整个 session rollback"有本质区别，后者会把本轮待提交的东西全带走。
        logger.warning("写入 agent_logs 失败（不影响本次回答）：%s", str(exc)[:200])
        return None


def prune_older_than(db: Session, days: int) -> "int | None":
    """删掉 N 天前的执行记录，返回删除条数；`days <= 0` 表示不清理、返回 0。

    ⚠️ **失败返回 `None`，不是 0。** 调用方（`agent_service._prune_agent_logs_if_due`）
    靠这个区别决定"下一次什么时候再试"：0 表示"清完了、按正常节流走"，
    `None` 表示"出错了、过一会儿再来"。两者都返回 0 的话，一次失败会被当成清理成功，
    于是要空等一整个节流窗口（一小时）才会重试。

    表结构上 `created_at` 有索引，所以这条范围删除是走索引的。

    **不 commit**：口径与 `create` 一致 —— 事务边界归 service 层。

    ⚠️ 清理放在 **SAVEPOINT**（`begin_nested`）里，失败只回滚这一小段。
    为什么必须是保存点而不是"捕获异常就算了"：这条删除跑在"本轮问答已经写了一半"的事务里
    （审计 + 对话都在同一个事务里待提交），所以清理出错的**影响面必须被切断**——
    保存点让"少清几条旧日志"和"本轮问答"彻底解耦，回滚点由我们自己指定，
    而不是赌 session 出错后还刚好能用。

    ⚠️ 但**别把它的能力说过头**（这点实测过，见下）：它防的是"语句级 / flush 级错误
    把外层事务带坏"，**防不住整事务级故障**——MySQL 的死锁(1213)、连接断开会让 InnoDB
    把整个事务回滚，那种情况下本轮问答**两种实现都保不住**（保存点也活不过整事务回滚）。
    换句话说：这是一个稳定的兜底写法，不是"死锁保险"。想真正防死锁只能把清理挪到
    独立事务里去，那是另一个量级的改动，当前不值得。
    """
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    removed = 0
    try:
        with db.begin_nested():
            # 刻意**不 flush**（别顺手加回来）：这是一条 Core DELETE，`rowcount` 直接可用，
            # 不需要 flush 帮忙；而 session 是 `autoflush=False`，所以保存点里只有这一条删除。
            # 若在这里 flush，任何"已 add 但未提交"的行都会被写进保存点 —— 回滚保存点时它们在
            # 库层面被撤掉，只能靠 SQLAlchemy 的状态恢复机制在 commit 时重发。那种隐式依赖不该
            # 留着：换个版本、或上游改一下写入顺序，语义就可能悄悄变。
            result = db.execute(delete(AgentLog).where(AgentLog.created_at < cutoff))
            removed = result.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理过期 agent_logs 失败（不影响本次回答）：%s", str(exc)[:200])
        return None
    return removed
