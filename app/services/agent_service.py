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
    return agent_log_repository.create(db, **fields) is not None


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
