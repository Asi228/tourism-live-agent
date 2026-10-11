"""Agent 问答的编排。

它只做四件事，其余全交给状态机：
  1. 装配图（把 db / user 闭包进节点）
  2. 设置**硬闸门**：`recursion_limit`（LangGraph 自带的防线）
  3. 把结果整理成响应，并把**执行轨迹**落进 agent_logs、**对话内容**落进 messages
  4. 失败时给一句人话（上游原文只进日志）

与 `/api/chat`（纯 RAG）的关系：两者共用同一个检索工具、同一份系统提示词、
同一套引用编号规则、同一套会话记忆 —— 差别只在"走几步、怎么决定走哪步"。
所以两边的答案口径是一致的，不存在"Agent 答得更放飞"的问题。
"""

import logging
import time

from sqlalchemy.orm import Session

from app.agent.graph import ANSWER_NO_EVIDENCE, build_graph
from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.user import User
from app.repositories import agent_log_repository
from app.services import conversation_service

logger = logging.getLogger(__name__)


def _write_log(db: Session, **fields) -> bool:
    """写一条审计日志 —— **只 flush，不提交**，返回是否写成功。

    **事务边界在 service 层**：repository 只 `flush()`，提交由调用方决定。
    不在这里 commit 的原因：本轮要落的东西不止审计，还有对话消息 ——
    分两次提交会留下"有审计没对话"或反过来，一次 commit 才能保证它们同进同出。

    repo 内部已吞掉异常并返回 None（写不进去也不会让用户拿不到答案），
    失败时它自己 rollback 清毒，所以调用方可以安全地接着写对话历史。
    """
    ok = agent_log_repository.create(db, **fields) is not None
    if ok:
        _prune_agent_logs_if_due(db)
    return ok


# 过期审计清理的节流窗口。项目里没有调度器，所以清理只能"机会式"地挂在写入路径上；
# 没有节流的话就变成**每次提问都发一条 DELETE** —— 那是拿日常性能换一个低频需求。
# 多 worker 时各自计时、每小时各清一次：重复执行没有副作用（删的是同一批旧行）。
_AGENT_LOG_PRUNE_INTERVAL_SECONDS = 3600.0

# ⚠️ 清理**失败**后的重试窗口。失败也推进哨兵，但只推进一个**短窗口**，两个极端都不要：
#   · 完全不推进 → 失败若是持续性的（表被改名、权限丢了），就变成**每个请求都发一条 DELETE**，
#     节流等于白做 —— 而这正是当初加节流的原因；
#   · 推进整个窗口 → 静默停摆一小时。agent 问答是低频路径，可能很久都没人发现。
# 取"5 分钟后再试"：既不是每请求重试，也不会长时间不试。
_AGENT_LOG_PRUNE_RETRY_SECONDS = 300.0

# ⚠️ 哨兵必须用 **None**，不能用 0.0。
#
# 这里踩过一次真坑：原先写 `_last_prune_at = 0.0` 表示"从未清理过"，
# 而 `time.monotonic()` **不是墙上时钟，是"开机以来的秒数"** ——
# 机器刚起时它可能**小于** 3600，于是首次判断 `now - 0.0 < 3600` 成立，
# "从未清理过"被误判成"刚清理过"：**清理静默失效，一直要等到开机满 1 小时**。
# 更恶心的是它不报错、不告警，而且测试会"随机器开机时长时绿时红"（开久了的机器上一直是绿的）。
_last_prune_at: "float | None" = None


def _prune_due(now: float, last: "float | None", interval: float) -> bool:
    """是否该清理了。`last is None` = 从未清理过 → **一定要清**。

    抽成纯函数是为了能被直接测：节流规则里"None 与 0.0 不等价"这件事
    很难靠集成测试钉住（它依赖机器开机时长），但在这里一行就能钉死。
    """
    return last is None or now - last >= interval


def _prune_agent_logs_if_due(db: Session) -> None:
    """按 `AGENT_LOG_RETENTION_DAYS` 清理过期审计（带节流，失败只 warning）。

    ⚠️ 哨兵按**结果**推进：成功推进整个节流窗口，失败只推进一个短窗口
    （见 `_AGENT_LOG_PRUNE_RETRY_SECONDS`）。所以 `prune_older_than` 必须能区分
    "清完了"（0）与"出错了"（None）—— 两者都返回 0 的话，失败会被当成清完了。
    """
    global _last_prune_at

    days = settings.AGENT_LOG_RETENTION_DAYS
    if days <= 0:
        return

    now = time.monotonic()
    if not _prune_due(now, _last_prune_at, _AGENT_LOG_PRUNE_INTERVAL_SECONDS):
        return

    removed = agent_log_repository.prune_older_than(db, days)

    if removed is None:
        # 失败（repo 已吞掉异常并 warning）：把哨兵设成"距窗口结束只剩 RETRY 秒"，
        # 于是 5 分钟后就会再试，而不是等满一小时、也不是每个请求都试。
        _last_prune_at = now - (
            _AGENT_LOG_PRUNE_INTERVAL_SECONDS - _AGENT_LOG_PRUNE_RETRY_SECONDS
        )
        return

    _last_prune_at = now
    if removed:
        logger.warning("已清理 %s 条超过 %s 天的 agent_logs", removed, days)


def answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
    conversation_id: int | None = None,
) -> dict:
    """跑一次 Agent 问答。

    `conversation_id` 为空时隐式新建会话（Phase 9），新 id 会放进响应。
    """
    if not settings.AGENT_ENABLED:
        raise AppException(
            ErrorCode.PARAM_ERROR, "Agent 功能当前未启用，请改用 /api/chat"
        )

    started = time.monotonic()

    # 会话与历史。放在调用图**之前**：归属校验失败就该立刻 404，
    # 而不是白跑一整轮多步推理（那可能是好几次 LLM 调用）。
    conv = conversation_service.ensure_conversation(db, user, conversation_id, query)
    history = conversation_service.load_history(db, conv.id)

    graph = build_graph(db, user, kb_ids, top_k)
    initial = {
        "query": query,
        "kb_ids": kb_ids,
        "top_k": top_k or settings.RAG_TOP_K,
        "history": history,
        "retrieved": [],
        "iterations": 0,
        "steps": [],
        "usage": {},
    }

    # 每轮检索要走 retrieve + evaluate 两个节点，再加上 analyze 和 generate，
    # 留一点余量。它是"最后一道保险"——正常路径会先被 AGENT_MAX_ITERATIONS 拦住。
    recursion_limit = max(8, settings.AGENT_MAX_ITERATIONS * 2 + 6)

    try:
        final = graph.invoke(initial, config={"recursion_limit": recursion_limit})
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        # 按**类名**判断而不是 import 具体的异常类：langgraph 的异常模块路径在
        # 小版本间挪过位置，按类名判断更耐升级。
        if type(exc).__name__ == "GraphRecursionError":
            message = "Agent 步骤数超出上限已中止，请把问题拆小一点再问"
            logger.warning("Agent 触发 recursion_limit=%s", recursion_limit)
        else:
            message = "Agent 执行失败，请稍后重试"
            logger.warning("Agent 执行失败: %s", str(exc)[:300])

        # 先 rollback 再写日志：图谱执行到一半失败时，session 可能停在需要清理的状态上。
        # 此刻没有任何业务改动要放弃（Agent 全程只读），rollback 只是清毒；
        # 之后这条审计仍能独立提交 —— 审计不与业务事务同生共死。
        # （本轮对话**不落库**：这次问答没成功，历史里不该留半截。
        #  只 flush 未提交的新会话也会随 session 关闭自动消失。）
        db.rollback()
        if _write_log(
            db, user_id=user.id, query=query, answer=None, hit=False, source_count=0,
            iterations=0, steps=None, total_tokens=0, elapsed_ms=elapsed,
            error_msg=message,
        ):
            db.commit()
        raise AppException(ErrorCode.LLM_ERROR, message) from exc

    elapsed = int((time.monotonic() - started) * 1000)
    steps = final.get("steps", [])
    usage = final.get("usage") or None
    sources = final.get("sources", [])
    iterations = final.get("iterations", 0)
    result = {
        "query": query,
        "answer": final.get("answer") or "",
        "hit": bool(final.get("hit")),
        "sources": sources,
        # **真实检索范围**，由 retrieve 节点从检索层带出来（已与用户权限求过交集）。
        # 不能回显入参 kb_ids：那个没求过交集，而本字段 None 的语义是「管理员不受限」，
        # 回显会把普通员工标成"扫全库"。
        # 判定为"不需要检索"时 iterations=0：本次**没查任何库**，给 [] 表示这一点；
        # 用 None 会和"不限范围"撞语义。
        "searched_kb_ids": final.get("searched_kb_ids") if iterations else [],
        "answer_mode": final.get("answer_mode") or ANSWER_NO_EVIDENCE,
        "iterations": iterations,
        "steps": steps,
        "usage": usage,
        "elapsed_ms": elapsed,
        "conversation_id": conv.id,
    }

    # 顺序有讲究：**先审计、再对话、最后统一提交**。
    # 万一审计写失败，repo 内部会 rollback 清毒 —— 那时对话数据还没写进去，
    # 所以不会被一起带走（反过来的顺序会让"审计失败"升级成"这轮对话丢了"）。
    _write_log(
        db, user_id=user.id, query=query, answer=result["answer"], hit=result["hit"],
        source_count=len(sources), iterations=result["iterations"], steps=steps,
        total_tokens=(usage or {}).get("total_tokens") or 0, elapsed_ms=elapsed,
    )
    conversation_service.append_turn(
        db,
        conv,
        query,
        result["answer"],
        sources=sources,
        meta={
            "source": "agent",     # 前端回放历史时据此决定要不要画执行轨迹
            "hit": result["hit"],
            "answer_mode": result["answer_mode"],
            "model": settings.LLM_MODEL,
            "usage": usage,
            "iterations": iterations,
            "searched_kb_ids": result["searched_kb_ids"],
            "elapsed_ms": elapsed,
            # 轨迹一起存：它是"这次为什么绕了两圈"的唯一证据。
            # 体积很小（每步就 node/ms 加几个字段），但回放历史时没有它就只能干瞪眼。
            "steps": steps,
        },
    )
    db.commit()

    logger.info(
        "Agent 问答完成 user=%s conv=%s 历史=%s轮 节点数=%s 检索轮数=%s 模式=%s "
        "引用=%s tokens=%s 耗时=%sms",
        user.username, conv.id, len(history) // 2, len(steps), result["iterations"],
        result["answer_mode"], len(sources), (usage or {}).get("total_tokens"), elapsed,
    )
    return result
