"""RAG 问答编排：检索 → 拼 Prompt → 生成 → 回答案与引用。

三个刻意的设计（都是 Phase 6 定下的口径）：

1. **检索完全复用 `vector_service.search()`**，不另开一条检索路径。
   权限过滤、空范围直接返回空、孤儿向量跳过……全都在那一层解决过了，
   这里再写一遍只会制造"两条路径行为不一致"的隐患。

2. **没命中就不调大模型**（`hit=False`，直接返回固定提示语）。
   企业知识库的价值是"答案必须来自本公司文档"；把空上下文交给模型，
   它会用通用常识回答（"年假一般 5 天"），员工会当成公司规定 —— 这是幻觉。
   顺带省下一笔完全没必要的 token。

   ⚠️ 实测补充（2026-10-03，真机端到端）：**Milvus 没有相似度阈值**，
   只要用户有可访问的知识库，它总会返回 top_k 条。所以"库里没有这个内容"
   这种情况 `hit` 仍然是 true —— 这条分支真正兜住的是「一个可访问的库都没有」
   （未授权的用户、或登录用户尚无任何授权库）。
   而"检索回来的资料其实不相关"由系统提示词第 3 条兜底：实测问库里没有的
   「公司食堂几点开饭」时，模型明确回答"根据现有资料无法回答该问题"，
   没有拿常识编答案 —— 这正是企业知识库该有的行为。
   （当时最高分 0.5182，而真正相关的两条是 0.7471 / 0.7717。
   要不要加相似度阈值过滤，见交付说明里的待定项。）

3. **多轮与落库（Phase 9 起）**：每次问答都会落到 `conversations` / `messages`，
   并把最近 `CONVERSATION_MAX_HISTORY_TURNS` 轮的问答对带进 Prompt。
   注意**检索范围不跟着会话走** —— `kb_ids` 是"这一问想查哪些库"的意图，
   用户随时会改（这次只查员工手册、下次查全部），锁在会话上反而会让改了不生效。

   ⚠️ 已知限制：历史只是**照原样拼进 Prompt**，没有做"指代消解"。
   所以"它的天数是多少"这类省略主语的追问，检索仍然按字面去搜，效果有限。
   要做对需要先让模型把问题改写完整（query rewriting）再检索 —— 那是后续的事。
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db.session import SessionLocal
from app.models.conversation import Conversation
from app.models.user import User
from app.rag import llm, prompt
from app.services import conversation_service, memory_service, vector_service

logger = logging.getLogger(__name__)

# 流式路径落库要**自己开一个 session**（理由见 `_persist_after_stream`）。
# 做成模块级变量而不是每次直接调 `SessionLocal()`，是为了给测试一个明确的替换点：
# 用例可以把它指向测试库，而不用去猜"该 patch 哪个模块里的哪个名字"。
_session_factory = SessionLocal


def _no_hit(
    query: str,
    scope: list[int] | None,
    found: dict | None = None,
    conversation_id: int | None = None,
) -> dict:
    """没有可用资料时的统一返回 —— **不调用大模型**。

    两条路径共用：检索一条都没返回；或返回了但一条都拼不进上下文。
    共用是为了保证两者的响应形状完全一致（前端只看 hit 就够，不用区分）。
    `found` 是检索层的原始返回，用来把"没重排的原因""跳过的孤儿数"一并带出去。

    注意：**没命中也会落进会话历史**（见 answer 末尾）—— 用户问过什么，
    下一轮和刷新后的列表里都该看得到，不能因为"没找到资料"就从记录里消失。
    """
    found = found or {}
    return {
        "query": query,
        "answer": prompt.NO_HIT_ANSWER,
        "hit": False,
        "sources": [],
        "searched_kb_ids": scope,
        "model": None,          # 没调用模型，所以是 null 而不是模型名
        "usage": None,
        "finish_reason": None,
        "reranked": False,
        "rerank_skipped_reason": found.get("rerank_skipped_reason"),
        "skipped_orphans": found.get("skipped_orphans", 0),
        "conversation_id": conversation_id,
    }


@dataclass
class Prepared:
    """一次问答的"准备结果" —— `answer()` 与 `stream_answer()` **共用**（见 `_prepare`）。"""

    # ⚠️ 只在"请求 session 还活着"的窗口内可用。FastAPI 的 yield 依赖（含 `get_db`
    # 的 `db.close()`）在**响应体开始迭代之前**就退出了 —— 流式路径拿到的 Prepared
    # 里的 conv 已经是 detached 的，读任何属性都是 DetachedInstanceError。
    # 所以流式永远只取纯数据（见 `_StreamPlan`），绝不碰这个字段。
    conv: Conversation
    history: list[tuple[str, str]]
    found: dict
    scope: list[int] | None
    sources: list[dict]
    no_hit: bool
    # 只有 no_hit=False 时才有意义
    system: str = ""
    user_message: str = ""
    summary_chars: int = 0
    memory_chars: int = 0


def _prepare(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None,
    top_k: int | None,
    conversation_id: int | None,
) -> Prepared:
    """会话准备 + 检索 + 拼上下文 —— **全项目只此一份**，两条问答路径共用。

    抽出来的理由和当年 `prompt.build_prompt_and_sources()` 一样：这段逻辑同时决定
    "引用编号对不对""会不会拿空资料去调模型""越权会不会白花一次检索的钱"，
    两处各写一遍必然改一处忘一处 —— 而"非流式答得对、流式答得歪"是最难查的那种 bug。

    ⚠️ **调用方必须把它放在"响应开始之前"**：它可能抛 `AppException`
    （越权 404、会话配额 40900、Milvus 50004、重排相关的 50007）。
    对流式尤其要紧 —— 响应头一旦发出去，就再也回不了统一信封了。
    """
    top_k = top_k or settings.RAG_TOP_K

    # 会话与历史要在检索**之前**准备好：拿了别人的 conversation_id 就该立刻 404，
    # 不该先白花一次检索和一次生成的价钱。
    conv = conversation_service.ensure_conversation(db, user, conversation_id, query)
    history = conversation_service.load_history(db, conv.id)

    # rerank=True：问答这一侧默认走「多召回 + 精排」（Phase 7）。
    # 是否真的重排还取决于 settings.RAG_RERANK_ENABLED，且重排失败会自动降级 ——
    # 结果里带 reranked 字段，能看出这次到底排没排。
    found = vector_service.search(db, user, query, kb_ids=kb_ids, top_k=top_k, rerank=True)
    results = found["results"]
    scope = found["searched_kb_ids"]

    # 没检索到 → 不调大模型（见文件头第 2 条）
    if not results:
        logger.info("RAG 未命中，跳过 LLM user=%s query=%r", user.username, query[:50])
        return Prepared(conv, history, found, scope, [], no_hit=True)

    context, adopted, sources = prompt.build_prompt_and_sources(
        results, settings.RAG_MAX_CONTEXT_CHARS
    )

    # 检索有结果，但一条都拼不进上下文（RAG_MAX_CONTEXT_CHARS 配得过小、
    # 或所有命中的正文都是空白）。**这时绝不能去调大模型** —— 那是拿空资料作答，
    # 白花钱且违背"答案必须来自文档"。config 里已有下限校验，这里是运行时最后一道。
    if not adopted:
        logger.warning(
            "检索到 %s 条但上下文为空，跳过 LLM user=%s RAG_MAX_CONTEXT_CHARS=%s",
            len(results), user.username, settings.RAG_MAX_CONTEXT_CHARS,
        )
        return Prepared(conv, history, found, scope, [], no_hit=True)

    # 长期记忆（Phase 11）与对话摘要（Phase 12）都在**这里**才取：
    # 没命中、没上下文的路径都不该白查一次库。
    memory = memory_service.section_for_prompt(db, user)
    summary = conversation_service.summary_section(conv)
    return Prepared(
        conv=conv,
        history=history,
        found=found,
        scope=scope,
        sources=sources,
        no_hit=False,
        system=prompt.build_system_prompt(memory_section=memory, summary_section=summary),
        user_message=prompt.build_user_message(query, context),
        summary_chars=len(summary),
        memory_chars=len(memory),
    )


def _turn_meta(data: dict, source: str) -> dict:
    """写进 `messages.meta` 的展示用字段 —— 两条问答路径共用，口径必须一致。"""
    return {
        # source 让前端回放历史时知道这条是哪个接口答的（纯 RAG 没有轨迹，
        # 渲染方式与 Agent 不同）。两个 service 都写这个字段，口径一致。
        "source": source,
        "hit": data.get("hit"),
        "model": data.get("model"),
        "usage": data.get("usage"),
        # 主要为 `length` 这一个值而存：它意味着答案被 max_tokens 截断，用户看到的
        # 是半截话。必须**进 meta** —— 否则刷新页面、或翻历史回放时提示就消失了，
        # 而"这条答案是被截断的"恰恰是回放时才最容易察觉的。
        # （Agent 路径没有这个字段：它内部多步调用，末步的 finish_reason 无意义。）
        "finish_reason": data.get("finish_reason"),
        "reranked": data.get("reranked"),
        "rerank_skipped_reason": data.get("rerank_skipped_reason"),
        "skipped_orphans": data.get("skipped_orphans", 0),
        "searched_kb_ids": data.get("searched_kb_ids"),
    }


def answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
    conversation_id: int | None = None,
) -> dict:
    """一次问答（**非流式**）。返回值结构与 schemas/chat.py 的 ChatResponse 对应。

    `conversation_id` 为空时会在本次**隐式新建**一个会话（Phase 9），
    并把新 id 放进响应；前端后续轮次带上它，就能接着上文问。
    """
    prep = _prepare(db, user, query, kb_ids, top_k, conversation_id)
    conv = prep.conv

    if prep.no_hit:
        return _persist(db, conv, query, _no_hit(query, prep.scope, prep.found, conv.id))

    got = llm.chat(prep.system, prep.user_message, history=prep.history)

    logger.info(
        "RAG 问答完成 user=%s conv=%s 历史=%s轮 summary=%s字 memory=%s字 scope=%s hits=%s "
        "adopted=%s reranked=%s answer_len=%s total_tokens=%s",
        user.username,
        conv.id,
        len(prep.history) // 2,
        prep.summary_chars,
        prep.memory_chars,
        "ALL" if prep.scope is None else prep.scope,
        len(prep.found["results"]),
        len(prep.sources),
        prep.found["reranked"],
        len(got["answer"]),
        (got["usage"] or {}).get("total_tokens"),
    )
    return _persist(db, conv, query, {
        "query": query,
        "answer": got["answer"],
        "hit": True,
        "sources": prep.sources,
        "searched_kb_ids": prep.scope,
        "model": settings.LLM_MODEL,
        "usage": got["usage"],
        "finish_reason": got["finish_reason"],
        "reranked": prep.found["reranked"],
        "rerank_skipped_reason": prep.found.get("rerank_skipped_reason"),
        "skipped_orphans": prep.found.get("skipped_orphans", 0),
        "conversation_id": conv.id,
    })


def _persist(db: Session, conv, query: str, data: dict) -> dict:
    """把这一轮问答写进会话并提交（Phase 9）。

    三条返回路径（未命中 / 上下文为空 / 正常生成）都从这里出去 ——
    它们是"这一问怎么了"，但对会话历史来说都只是**一轮对话**，都该被记下来。

    提交放在这里而不是 repository：事务边界归 service 层（项目硬约定）。
    反过来，如果生成阶段抛了异常，这个函数根本不会被执行 ——
    那个只 flush 未提交的新会话会随 session 关闭自动消失，不会留下空壳。
    """
    conversation_service.append_turn(
        db,
        conv,
        query,
        data["answer"],
        sources=data.get("sources") or [],
        meta=_turn_meta(data, "rag"),
    )
    db.commit()
    return data


# ==================== 流式（SSE）====================
#
# 事件协议（前端 `frontend/src/api/stream.js` 按这个解析）：
#   event: meta    data: {hit, sources, searched_kb_ids, reranked, …}   ← 先给引用，再吐字
#   event: delta   data: {text: "…"}                                    ← 逐块，多块
#   event: done    data: {answer, usage, finish_reason, conversation_id, saved}
#   event: error   data: {code, message}                                ← 只在"已经开始吐字之后"才可能
#
# ⚠️ 两条例外必须写进 README，因为它们违背"HTTP 恒 200、错误码在 body"这条通用约定：
#   ① **流内错误不再有 body.code** —— 只能用 event: error 表达（响应头早已发出，改不了状态码）；
#   ② **鉴权/限流失败拿到的不是 SSE，是普通信封** —— 那两个依赖在响应开始前求值。
#      所以前端两条路都得能解析（按 content-type 分流）。


@dataclass
class _StreamPlan:
    """流式生成所需的全部输入 —— **刻意不含任何 ORM 对象**。

    `_prepare` 是在请求 session 里跑的，而 FastAPI 的 yield 依赖在响应体开始迭代
    **之前**就退出了（`db.close()` 已执行）。流式生成阶段手里只有一个 detached 的
    `Prepared.conv`，谁顺手写一句 `prep.conv.xxx` 就是"测试跑不出来、一上生产就炸"
    的路径。所以 `stream_answer` 在返回生成器**之前**把需要的字段拷进这里，
    让流式代码根本摸不到 ORM 对象。
    """

    no_hit: bool
    history: list[tuple[str, str]]
    found: dict
    scope: list[int] | None
    sources: list[dict]
    system: str = ""
    user_message: str = ""


def stream_answer(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int | None = None,
    conversation_id: int | None = None,
) -> Iterator[dict]:
    """流式问答：返回 SSE 事件生成器。

    ⚠️ **这个函数本身不是生成器**（函数体里没有 `yield`），它是**普通函数**：
    先把所有可能抛异常的准备工作做完（`_prepare` 里的越权 404 / 配额 40900 /
    Milvus 50004），**再**返回内部生成器。

    这个区分是刻意的、也是必须的：FastAPI 只会在"迭代 body_iterator"时才发响应头，
    所以只要准备工作发生在 `return` 之前，那些失败就还能走统一信封给前端一个
    `{code: 40400}`；反过来，一旦写成了 `yield` 开头的生成器，准备工作就被推到
    "响应已开始"之后，越权就只能变成一个 200 流里的事件了 —— 那是降级。

    返回生成器之前把 `Prepared` 拷成 `_StreamPlan`（纯数据）：请求 session 在
    响应开始前就关了，流式阶段不能再摸任何 ORM 对象。
    """
    prep = _prepare(db, user, query, kb_ids, top_k, conversation_id)
    plan = _StreamPlan(
        no_hit=prep.no_hit,
        history=prep.history,
        found=prep.found,
        scope=prep.scope,
        sources=prep.sources,
        system=prep.system,
        user_message=prep.user_message,
    )
    return _stream_events(plan, user.id, query, conversation_id)


def _stream_events(
    plan: _StreamPlan,
    user_id: int,
    query: str,
    conversation_id: int | None,
) -> Iterator[dict]:
    """真正产事件的地方（`_prepare` 已经跑完，这里只负责生成与落库）。

    ⚠️ 入参是 `_StreamPlan`（纯数据）而不是 `Prepared`：见该类说明 ——
    请求 session 已经关了，这里没有也不该有 ORM 对象。

    落库分两步（见 `_persist_after_stream` / `_maintain_after_stream`）：
    先写消息并 `yield done` 把答案交付出去，**再**补摘要与裁剪。
    """

    # ---- 没命中：不调模型，答案是一句固定话术，同样走 delta 让前端只写一套渲染 ----
    if plan.no_hit:
        data = _no_hit(query, plan.scope, plan.found, conversation_id)
        yield {"event": "meta", "data": _meta_event(data, conversation_id)}
        yield {"event": "delta", "data": {"text": data["answer"]}}
        saved_id = _persist_after_stream(user_id, conversation_id, query, data)
        yield {"event": "done", "data": {
            "answer": data["answer"],
            "usage": None,
            "finish_reason": None,
            "conversation_id": saved_id,
            "saved": saved_id is not None,
        }}
        if saved_id is not None:
            _maintain_after_stream(saved_id)
        return

    # ---- 有资料：先给引用，再逐字吐 ----
    yield {"event": "meta", "data": {
        "hit": True,
        "sources": plan.sources,
        "searched_kb_ids": plan.scope,
        "reranked": plan.found["reranked"],
        "rerank_skipped_reason": plan.found.get("rerank_skipped_reason"),
        "skipped_orphans": plan.found.get("skipped_orphans", 0),
        "model": settings.LLM_MODEL,
        # 只在**客户端本来就传了**的时候回显：新建的会话要等落库成功才有权威 id
        # （`_prepare` 里那个是新会话的临时对象，不 commit，随请求结束就没了）。
        "conversation_id": conversation_id,
    }}

    parts: list[str] = []
    usage: dict | None = None
    finish_reason: str | None = None
    try:
        for chunk in llm.stream_chat(
            plan.system, plan.user_message, history=plan.history
        ):
            if chunk["type"] == "delta":
                parts.append(chunk["text"])
                yield {"event": "delta", "data": {"text": chunk["text"]}}
            else:
                usage = chunk.get("usage")
                finish_reason = chunk.get("finish_reason")
    except AppException as exc:
        # 流已经开始了，状态码改不了 —— 只能用事件告诉前端。
        # **不落库**：半截答案进 messages 会污染后续的历史喂回（模型看到"说到一半"
        # 的话术更糟），与"LLM 失败不留空壳"是同一个口径。
        logger.warning(
            "流式生成中断，本轮不落库 conv=%s code=%s err=%s",
            conversation_id, exc.code, exc.message,
        )
        yield {"event": "error", "data": {"code": exc.code, "message": exc.message}}
        return

    answer = "".join(parts).strip()
    if not answer:
        # 一个字都没吐：同上，不落库（空答案存进去只会变成一条空消息）
        logger.warning("流式生成返回空内容，本轮不落库 conv=%s", conversation_id)
        yield {"event": "error", "data": {
            "code": ErrorCode.LLM_ERROR,
            "message": "大模型未返回内容，请稍后重试",
        }}
        return

    logger.info(
        "RAG 流式问答完成 user_id=%s conv=%s 历史=%s轮 hits=%s adopted=%s "
        "answer_len=%s total_tokens=%s",
        user_id, conversation_id, len(plan.history) // 2,
        len(plan.found["results"]), len(plan.sources),
        len(answer), (usage or {}).get("total_tokens"),
    )

    data = {
        "query": query,
        "answer": answer,
        "hit": True,
        "sources": plan.sources,
        "searched_kb_ids": plan.scope,
        "model": settings.LLM_MODEL,
        "usage": usage,
        "finish_reason": finish_reason,
        "reranked": plan.found["reranked"],
        "rerank_skipped_reason": plan.found.get("rerank_skipped_reason"),
        "skipped_orphans": plan.found.get("skipped_orphans", 0),
    }
    saved_id = _persist_after_stream(user_id, conversation_id, query, data)
    yield {"event": "done", "data": {
        "answer": answer,
        "usage": usage,
        "finish_reason": finish_reason,
        "conversation_id": saved_id,
        "saved": saved_id is not None,
    }}
    if saved_id is not None:
        _maintain_after_stream(saved_id)


def _meta_event(data: dict, conversation_id: int | None) -> dict:
    """`meta` 事件的载荷 —— 前端靠它在答案之前先把引用与口径标签画出来。"""
    return {
        "hit": data.get("hit"),
        "sources": data.get("sources") or [],
        "searched_kb_ids": data.get("searched_kb_ids"),
        "reranked": data.get("reranked"),
        "rerank_skipped_reason": data.get("rerank_skipped_reason"),
        "skipped_orphans": data.get("skipped_orphans", 0),
        "conversation_id": conversation_id,
    }


def _persist_after_stream(
    user_id: int, conversation_id: int | None, query: str, data: dict
) -> int | None:
    """流式路径的落库：**自己开一个 session**，成功返回会话 id，失败返回 None。

    为什么不能复用请求那个 session：Starlette 对**同步生成器**是用
    `iterate_in_threadpool` 迭代的，每次 `next()` 落在哪个线程不保证 ——
    而 SQLAlchemy 的 `Session` **不是线程安全的**。自己开一个，跨线程也没有共享状态。

    为什么失败**只 warning、不把这次问答判成失败**：答案已经完整地吐给用户了，
    这时候再报"请求失败"是错的。但"没存进历史"必须让前端知道（`done.saved=false`），
    否则用户刷新后发现刚才那轮不见了，却不知道该怪谁。

    会话是**到这里才真正创建**的：`_prepare` 里的 `ensure_conversation` 只 flush、
    随请求 session 关闭就回滚，所以"生成失败不留空壳"在流式下依然成立 ——
    失败路径根本走不到这里。

    摘要与裁剪**不在这里做**（`defer_maintenance=True`）：摘要是同步的模型调用，
    放在这一步会让"最后一个字吐完了、光标还在转"。它们由 `_maintain_after_stream`
    在 `done` 事件**之后**补做 —— 两步必须一起推迟（见 `append_turn` 的说明）。
    """
    try:
        with _session_factory() as db:
            user = db.get(User, user_id)
            if user is None:
                logger.warning("流式落库失败：用户已不存在 user_id=%s", user_id)
                return None
            conv = conversation_service.ensure_conversation(db, user, conversation_id, query)
            conversation_service.append_turn(
                db,
                conv,
                query,
                data["answer"],
                sources=data.get("sources") or [],
                meta=_turn_meta(data, "rag"),
                defer_maintenance=True,
            )
            db.commit()
            return conv.id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "流式问答落库失败（答案已返回给用户）user_id=%s: %s", user_id, str(exc)[:200]
        )
        return None


def _maintain_after_stream(conversation_id: int) -> None:
    """流收尾的"维护"：摘要 + 裁剪，在 `done` 交付完答案**之后**补做（失败只 warning）。

    为什么不跟落库放在一起：`maybe_refresh_summary` 里挂着一次**同步的模型调用**
    （最多 400 token），放在 done 之前，用户就得盯着转动的光标等它 —— 非流式时
    它藏在"整段响应"里看不出来，流式下是肉眼可见的停滞。挪到这里，答案已经
    交付完，模型调用再慢也不挡用户。

    顺序仍然恪守 P12 的硬约束：**先摘要、后裁剪**（`run_maintenance`）——
    裁剪会永久删掉滑出窗口的旧消息，摘要是它们唯一的底。两步同在一个新开的
    session 里按序执行，一起 commit。

    失败只 warning，失败方向是安全的：摘要字段没动、下一轮会重试（机会式）；
    裁剪晚一轮执行只是**多留**几条消息。客户端在 done 一到就断开时，生成器被
    关闭、这一步会跳过 —— 同样只是推迟，不丢数据。
    """
    try:
        with _session_factory() as db:
            conv = db.get(Conversation, conversation_id)
            if conv is None:
                logger.warning("流式收尾失败：会话已不存在 conv=%s", conversation_id)
                return
            conversation_service.run_maintenance(db, conv)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "流式收尾（摘要/裁剪）失败 conv=%s: %s", conversation_id, str(exc)[:200]
        )
