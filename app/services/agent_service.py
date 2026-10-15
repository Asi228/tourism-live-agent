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
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.agent.graph import ANSWER_NO_EVIDENCE, build_graph
from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db import session as db_session
from app.models.user import User
from app.repositories import agent_log_repository
from app.services import conversation_service

logger = logging.getLogger(__name__)


def _write_log(db: Session, **fields) -> bool:
    """写一条审计日志 —— **只 flush，不提交**，返回是否写成功。

    **事务边界在 service 层**：repository 只 `flush()`，提交由调用方决定。
    不在这里 commit 的原因：本轮要落的东西不止审计，还有对话消息 ——
    分两次提交会留下"有审计没对话"或反过来，一次 commit 才能保证它们同进同出。

    repo 内部已吞掉异常并返回 None（写不进去也不会让用户拿不到答案）。

    ⚠️ 注意"返回 None"的含义是**只有审计那一段回滚了**：repo 把它包在 SAVEPOINT 里，
    失败时回滚的是保存点，session 仍然干净、本轮事务里已经 flush 出来的东西
    （比如刚新建的会话行）**不会被带走**，所以调用方可以安全地接着写对话历史。
    （早期这里写的是"它自己 rollback 清毒"，把保护范围说过头了 —— 整个 session 的
    rollback 会让会话行一起消失，那才是危险的。）
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
        "summary": conversation_service.summary_section(conv),
        "retrieved": [],
        "iterations": 0,
        "fetched": False,
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
    # "取过数"与"检索轮数"是两件事：文档任务（fetch_docs）取到了全文，
    # 但 iterations=0（一次检索都没做）—— searched_kb_ids 的判定必须按"取没取过数"。
    # 旧状态形状没有 fetched 字段时退回旧判据（bool(iterations)），不至于报错。
    fetched = final.get("fetched")
    if fetched is None:
        fetched = bool(iterations)
    result = {
        "query": query,
        "answer": final.get("answer") or "",
        "hit": bool(final.get("hit")),
        "sources": sources,
        # **真实检索范围**，由 retrieve / fetch_docs 节点从取数层带出来（已与用户权限求过交集）。
        # 不能回显入参 kb_ids：那个没求过交集，而本字段 None 的语义是「管理员不受限」，
        # 回显会把普通员工标成"扫全库"。
        # 本次**没取过任何数**（寒暄 / 空检索）：给 [] 表示这一点；用 None 会和"不限范围"撞语义。
        "searched_kb_ids": final.get("searched_kb_ids") if fetched else [],
        "answer_mode": final.get("answer_mode") or ANSWER_NO_EVIDENCE,
        "iterations": iterations,
        "steps": steps,
        "usage": usage,
        "elapsed_ms": elapsed,
        "conversation_id": conv.id,
    }

    # 顺序有讲究：**先审计、再对话、最后统一提交**。
    # 万一审计写失败，repo 只回滚它自己那个 SAVEPOINT（见 `_write_log` 的说明），
    # 本轮已经 flush 出来的会话行**不会被带走** —— 这正是保存点在这里的全部意义。
    # 若改成"审计失败就回滚整个事务"，会级联出"会话没了、消息却还往里插"的
    # 孤儿数据（MySQL 上更糟：外键直接 1452，把一次成功的问答变成 500）。
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
            # 文档清单涉及的库（仅 LIST 路径有值，其余为 None）—— 清单正文带
            # 【库名】+《文件名》而没有 sources，撤权后的回放脱敏靠它判定
            # （见 conversation_service._cites_revoked_kb）。
            "catalog_kb_ids": final.get("catalog_kb_ids"),
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


# ==================== 流式（SSE，节点级进度）====================
#
# 与 rag_service 的流式**同协议、同套路**：
#     status → step*N → meta → done      （error 只在流开始之后）
# 差别只是中间那段不是逐字，而是**多步决策的进度** —— README 早就定了这个口径：
# Agent 的价值在"它为什么慢"（判定要不要检索、资料够不够、要不要换个词再查一轮），
# 而不在"答案一个字一个字地蹦"。真要逐字就得给 generate 节点注入一条输出通道
# （闭包塞一个 sink），复杂度再上一档，留给后续。
#
# ⚠️ **实现方式选了同步而非 async**：langgraph 同时提供同步的 `graph.stream()`，
# 选它就不必把端点改成 async 生成器，可以直接复用 P13 那一整套机制（普通函数 +
# 同步生成器 + Starlette 线程池迭代）。代价是心跳仍然发不出去 —— 与 rag_service
# 那条路的结论一致，等真要部署到反代后面再一起解决。

# 流式路径要自己开一个 session —— 统一走 `db_session.new_session()`；
# 理由（以及"测试怎么换库"）都写在那个函数上，见 app/db/session.py。


def stream_answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
    conversation_id: int | None = None,
) -> Iterator[dict]:
    """流式 Agent 问答：返回 SSE 事件生成器。

    ⚠️ **这个函数本身不是生成器**（函数体里没有 `yield`），是普通函数：先把准备
    工作跑完再返回内部生成器。理由与 `rag_service.stream_answer` 完全一致 ——
    FastAPI 只在开始迭代 body 时才发响应头，准备工作必须赶在那之前完成，
    "Agent 未启用" / 越权 404 / 配额到顶才能走统一信封，而不是降级成流里的 error。

    准备工作里做两件事：
      · **归属校验** —— 会话不是自己的要趁早 404（也顺带省下一整轮多步推理的钱）；
      · **新会话的配额检查** —— 必须在响应开始**之前**。

    ⚠️ 配额这一项曾经漏在这里（只做了 `get_owned`），而含配额检查的
    `ensure_conversation` 在生成器里 —— 结果是会话数到顶时 40900 降级成一个
    200 流里的事件，前端只看到"断流"，用户根本不知道是自己配额满了。
    RAG 那条路是对的（`_prepare` 在 return 之前就调了 `ensure_conversation`），
    这里对齐它。真正的**创建**仍然留在流式阶段（那里要用新 session）。

    历史、摘要、图本身都推迟到流式阶段 —— 因为那里必须用**新 session**，
    见 `_agent_stream_events` 的说明。
    """
    if not settings.AGENT_ENABLED:
        raise AppException(
            ErrorCode.PARAM_ERROR, "Agent 功能当前未启用，请改用 /api/chat"
        )
    if conversation_id is not None:
        conversation_service.get_owned(db, user, conversation_id)
    else:
        # 只**检查**、不创建：创建要等到流式阶段的新 session 里做。
        # 检查与创建之间有个极小的 TOCTOU 窗口（用户在别处同时开了新会话），
        # 但那个窗口的代价只是"少拦一次"，而把它放到响应之后代价是"错误表达不了"。
        conversation_service.ensure_room_for_new_conversation(db, user)

    return _agent_stream_events(user.id, query, kb_ids, top_k, conversation_id)


def _agent_stream_events(
    user_id: int,
    query: str,
    kb_ids: list[int] | None,
    top_k: int | None,
    conversation_id: int | None,
) -> Iterator[dict]:
    """真正产事件的地方（`stream_answer` 的准备工作已经跑完）。

    🔴 **图必须用这里新开的 session 装配，绝不能用请求 session**。
    `build_graph(db, user, ...)` 是**闭包捕获 db** 的（graph.py 的注释解释了为什么
    这么设计），而请求 session 在响应体开始迭代**之前**就关了（fastapi 0.115.6 的
    yield 依赖早退，P13 已坐实）。P13 那次踩的只是"流里碰了一个 ORM 对象"，这里踩的
    是**整个图的每个节点都在碰 db**（检索要读正文、长期记忆要读库）—— 一旦用错
    session，流式这条路会全线炸，而同步路径的测试一条都红不了。
    """
    started = time.monotonic()
    yield {"event": "status", "data": {"phase": "start"}}

    saved_id: int | None = None

    with db_session.new_session() as db:
        user = db.get(User, user_id)
        if user is None:
            logger.warning("Agent 流式：用户已不存在 user_id=%s", user_id)
            yield {"event": "error", "data": {
                "code": ErrorCode.NOT_FOUND, "message": "用户不存在",
            }}
            return

        conv = conversation_service.ensure_conversation(db, user, conversation_id, query)
        history = conversation_service.load_history(db, conv.id)

        graph = build_graph(db, user, kb_ids, top_k)
        initial = {
            "query": query,
            "kb_ids": kb_ids,
            "top_k": top_k or settings.RAG_TOP_K,
            "history": history,
            "summary": conversation_service.summary_section(conv),
            "retrieved": [],
            "iterations": 0,
            "steps": [],
            "usage": {},
        }
        recursion_limit = max(8, settings.AGENT_MAX_ITERATIONS * 2 + 6)

        # ---- 跑图：每完成一个节点就吐一个 step 事件 ----
        final: dict = {}
        failure: str | None = None
        try:
            for update in graph.stream(
                initial,
                config={"recursion_limit": recursion_limit},
                stream_mode="updates",
            ):
                for node_name, patch in update.items():
                    final.update(patch)
                    yield {"event": "step", "data": _step_event(node_name, patch)}
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.monotonic() - started) * 1000)
            if type(exc).__name__ == "GraphRecursionError":
                failure = "Agent 步骤数超出上限已中止，请把问题拆小一点再问"
                logger.warning("Agent 流式触发 recursion_limit=%s", recursion_limit)
            else:
                failure = "Agent 执行失败，请稍后重试"
                logger.warning("Agent 流式执行失败: %s", str(exc)[:300])

            # 同非流式路径：**先 rollback 再写审计**，让这条审计仍能独立提交。
            # 本轮对话不落库（多步推理没走完，历史里不该留半截）。
            db.rollback()
            if _write_log(
                db, user_id=user_id, query=query, answer=None, hit=False,
                source_count=0, iterations=0, steps=None, total_tokens=0,
                elapsed_ms=elapsed, error_msg=failure,
            ):
                db.commit()
            yield {"event": "error", "data": {
                "code": ErrorCode.LLM_ERROR, "message": failure,
            }}
            return

        elapsed = int((time.monotonic() - started) * 1000)
        steps = final.get("steps", [])
        usage = final.get("usage") or None
        sources = final.get("sources", [])
        iterations = final.get("iterations", 0)
        # 这几个值在 `with` 退出之后还要用 —— 提前取出来，届时 ORM 对象已经 detached
        answer = final.get("answer") or ""
        hit = bool(final.get("hit"))
        answer_mode = final.get("answer_mode") or ANSWER_NO_EVIDENCE
        # "取过数"与"检索轮数"分开判（见非流式同处的说明）：文档任务取到了全文、
        # 但 iterations=0 —— 用 iterations 判会把它的检索范围标成"没查任何库"。
        fetched = final.get("fetched")
        if fetched is None:
            fetched = bool(iterations)
        # 真实取数范围由 retrieve / fetch_docs 节点带出来（已与权限求过交集）。
        # 没取过数给 [] —— None 的语义是"管理员不受限"，不能混用。
        searched_kb_ids = final.get("searched_kb_ids") if fetched else []

        # 引用先给出去，让用户先看到"依据来自哪几份文档"
        yield {"event": "meta", "data": {
            "hit": hit,
            "sources": sources,
            "answer_mode": answer_mode,
            "iterations": iterations,
            "searched_kb_ids": searched_kb_ids,
            "model": settings.LLM_MODEL,
            # 只在客户端本来就传了时回显：新建的会话要等落库成功才有权威 id
            "conversation_id": conversation_id,
        }}

        # 顺序照搬非流式：**先审计、再对话、最后统一提交**。审计写失败时只回滚
        # 它自己的 SAVEPOINT，会话行留得住 —— 见 `_write_log` 的说明。
        # ⚠️ 这里还有一层非流式没有的风险：**整个 new session 的提交是一次性的**。
        # 审计/落库/提交任何一步抛异常，都会让这条流以一个没有内容的
        # `done` 收场（用户看着答案生成完了却什么都没收到）—— 所以下面包了 try。
        try:
            _write_log(
                db, user_id=user_id, query=query, answer=answer, hit=hit,
                source_count=len(sources), iterations=iterations, steps=steps,
                total_tokens=(usage or {}).get("total_tokens") or 0, elapsed_ms=elapsed,
            )
            conversation_service.append_turn(
                db,
                conv,
                query,
                answer,
                sources=sources,
                meta={
                    "source": "agent",     # 前端回放历史时据此决定要不要画执行轨迹
                    "hit": hit,
                    "answer_mode": answer_mode,
                    "model": settings.LLM_MODEL,
                    "usage": usage,
                    "iterations": iterations,
                    "searched_kb_ids": searched_kb_ids,
                    "elapsed_ms": elapsed,
                    # 轨迹一起存：它是"这次为什么绕了两圈"的唯一证据。
                    "steps": steps,
                    # 文档清单涉及的库（仅 LIST 路径有值）—— 撤权后的回放脱敏靠它判定，
                    # 与非流式的 meta 组装保持一致（两处都要加，漏一处就漏一半）。
                    "catalog_kb_ids": final.get("catalog_kb_ids"),
                },
                defer_maintenance=True,
            )
            db.commit()
            saved_id = conv.id

            logger.info(
                "Agent 流式问答完成 user_id=%s conv=%s 历史=%s轮 节点数=%s 检索轮数=%s "
                "模式=%s 引用=%s tokens=%s 耗时=%sms",
                user_id, conv.id, len(history) // 2, len(steps), iterations,
                answer_mode, len(sources), (usage or {}).get("total_tokens"), elapsed,
            )
        except Exception as exc:  # noqa: BLE001
            # 答案已经完整地算出来了，只是没能写进历史 —— 与 RAG 那条路同一口径：
            # **不能把它判成失败**（用户到手的答复不该因为一次 DB 抖动变成断流），
            # 与 `rag_service._persist_after_stream` 同一口径：给 `done{saved: false}`，
            # 让前端提示"这次没能存入历史"。
            # 不这么做的话，`saved` 这个字段在 Agent 路上恒为 true，等于废掉的守门。
            db.rollback()
            logger.warning(
                "Agent 流式问答落库失败（答案已返回给用户）user_id=%s: %s",
                user_id, str(exc)[:200],
            )

    # `with` 已退出（session 关闭）—— 下面只用纯值，绝不碰 ORM 对象
    yield {"event": "done", "data": {
        "answer": answer,
        "usage": usage,
        "answer_mode": answer_mode,
        "iterations": iterations,
        "steps": steps,
        "elapsed_ms": elapsed,
        "conversation_id": saved_id,
        "saved": saved_id is not None,
    }}
    if saved_id is not None:
        conversation_service.maintain_after_stream(saved_id)


def _step_event(node: str, patch: dict) -> dict:
    """一个节点完成时的事件载荷。

    取自 `graph._step` 已经攒好的轨迹项，再补一个 `node` 字段便于前端按节点名
    分组/着色。**刻意不带答案**：节点还可能走到失败分支，答案提前吐到界面上会
    变成"闪一下就没了"。答案统一在 `done` 里给。
    """
    # langgraph 在节点返回 None 时给出的是 `{"node": None}` —— 补丁本身不是 dict。
    # 不兜这一下就是 AttributeError，而且发生在**响应已经开始之后**：前端看不到
    # `error`（生成器直接炸了），只会见到一条静默断流。一行换掉一整类生产事故。
    patch = patch or {}
    trail = patch.get("steps") or []
    last = dict(trail[-1]) if trail else {}
    last.pop("node", None)
    return {"node": node, **last}


