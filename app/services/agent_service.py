"""Agent 问答的编排。

它只做四件事，其余全交给状态机：
  1. 装配图（把 db / user 闭包进节点）
  2. 设置**硬闸门**：`recursion_limit`（LangGraph 自带的防线）
  3. 把结果整理成响应，并把**执行轨迹**落进 agent_logs
  4. 失败时给一句人话（上游原文只进日志）

与 `/api/chat`（纯 RAG）的关系：两者共用同一个检索工具、同一份系统提示词、
同一套引用编号规则 —— 差别只在"走几步、怎么决定走哪步"。
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

logger = logging.getLogger(__name__)


def _write_log(db: Session, **fields) -> None:
    """写一条审计日志并提交。

    **事务边界在 service 层**：repository 只 `flush()`，提交由这里决定。
    这样将来 Phase 9 要在同一个事务里写 messages + agent_logs 时，不会有一个藏在
    repo 里、看起来人畜无害的提前 commit 把事务截断（造成部分提交）。

    repo 内部已吞掉异常并返回 None —— 审计写不进去不该让用户拿不到已生成好的答案，
    所以这里拿到 None 就跳过提交。
    """
    if agent_log_repository.create(db, **fields) is not None:
        db.commit()


def answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
) -> dict:
    """跑一次 Agent 问答。"""
    if not settings.AGENT_ENABLED:
        raise AppException(
            ErrorCode.PARAM_ERROR, "Agent 功能当前未启用，请改用 /api/chat"
        )

    started = time.monotonic()
    graph = build_graph(db, user, kb_ids, top_k)
    initial = {
        "query": query,
        "kb_ids": kb_ids,
        "top_k": top_k or settings.RAG_TOP_K,
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
        # 此刻没有任何业务改动要放弃（Agent 全程只读），所以 rollback 只是清毒，
        # 之后这条审计仍能独立提交 —— 审计不与业务事务同生共死。
        db.rollback()
        _write_log(
            db, user_id=user.id, query=query, answer=None, hit=False, source_count=0,
            iterations=0, steps=None, total_tokens=0, elapsed_ms=elapsed,
            error_msg=message,
        )
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
    }

    _write_log(
        db, user_id=user.id, query=query, answer=result["answer"], hit=result["hit"],
        source_count=len(sources), iterations=result["iterations"], steps=steps,
        total_tokens=(usage or {}).get("total_tokens") or 0, elapsed_ms=elapsed,
    )

    logger.info(
        "Agent 问答完成 user=%s 节点数=%s 检索轮数=%s 模式=%s 引用=%s tokens=%s 耗时=%sms",
        user.username, len(steps), result["iterations"], result["answer_mode"],
        len(sources), (usage or {}).get("total_tokens"), elapsed,
    )
    return result
