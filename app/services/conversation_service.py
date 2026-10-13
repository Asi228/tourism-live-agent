"""会话与消息的编排（Phase 9）。

它解决的是"**接着上一句继续问**"这件事，一共四件：

1. **会话归属**：拿到一个 `conversation_id` 时先确认它属于当前用户 ——
   否则甲就能读乙的对话。这里刻意用 **404（会话不存在）** 而不是 403：
   403 等于告诉对方"这个 id 是存在的，只是不归你"，白送一条枚举信息。
2. **历史拼装**：把最近 N 轮问答对取出来喂给模型。只取**完整轮次**（user 开头），
   截断点落在中间时把孤立的 assistant 丢掉 —— 一条没有对应问题的答案对模型是噪声。
3. **写入**：一轮落两条（user 问题 + assistant 答案）。
4. **回放脱敏**：撤销某个库的授权之后，历史会话不能变成绕开撤销的后门 ——
   引用了已无权库的答案在回放时整条隐藏（见 `load_messages_for_display`）。

事务边界在调用方（rag_service / agent_service）：本模块只 flush。
这样 LLM 调用失败时未提交的会话会自动消失，不会留下一堆"新会话"空壳。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db import session as db_session
from app.models.conversation import TITLE_MAX_LENGTH, Conversation
from app.models.message import ROLE_ASSISTANT, ROLE_USER, Message
from app.models.user import User
from app.rag import llm, prompt
from app.repositories import conversation_repository, message_repository
from app.services import knowledge_service

logger = logging.getLogger(__name__)

# 流式收尾（补摘要/裁剪）要自己开一个 session —— 统一走 `db_session.new_session()`，
# 理由见 `maintain_after_stream`；"测试怎么换库"见 app/db/session.py。

# 引用的资料已经无权访问时，用来替换答案正文的固定话术。
# 不用空字符串：空字符串在前端看起来像"这条消息坏了"，而这其实是一个**明确的状态**。
REDACTED_ANSWER = "（该轮答案所依据的资料，你已不再有访问权限，内容已隐藏）"


def _make_title(query: str) -> str:
    """用首个问题当标题 —— 会话列表里"年假可以休几天"比"新会话 7"好认得多。"""
    flat = " ".join((query or "").split())
    return flat[:TITLE_MAX_LENGTH] or "新会话"


# ==================== 会话 ====================


def get_owned(db: Session, user: User, conversation_id: int) -> Conversation:
    """只校验归属，**绝不创建** —— 给"读/删指定会话"这类操作使用。

    不存在 / 不属于自己 → 都报"会话不存在"（见文件头第 1 条）。
    """
    conv = conversation_repository.get_by_id(db, conversation_id)
    if conv is None or conv.user_id != user.id:
        logger.warning("会话不存在或越权 user_id=%s conv_id=%s", user.id, conversation_id)
        raise AppException(ErrorCode.NOT_FOUND, "会话不存在")
    return conv


def ensure_conversation(
    db: Session, user: User, conversation_id: int | None, first_query: str
) -> Conversation:
    """拿到本次要写入的会话：给了 id 就校验归属，没给就**隐式创建**。

    隐式创建的理由：前端"直接开问"是最自然的交互，不该逼用户先点一下"新建会话"。
    创建只在内存里 flush（拿自增 id），提交由调用方在回答生成成功后做 ——
    这样 LLM 失败时不会留下空会话。
    """
    if conversation_id is None:
        _ensure_room_for_new_conversation(db, user)
        conv = conversation_repository.create(db, user.id, _make_title(first_query))
        logger.info("新建会话 user=%s conv_id=%s", user.username, conv.id)
        return conv
    return get_owned(db, user, conversation_id)


def _ensure_room_for_new_conversation(db: Session, user: User) -> None:
    """新建会话前先看配额，超了**拒绝并提示**。

    为什么不是"悄悄删掉最旧的会话"：那等于替用户决定"哪段对话可以不要了"。
    用户对历史对话的预期是只增不减，静默删除比让他自己面对 200 个旧会话糟糕得多
    —— 前者是数据损坏，后者只是麻烦。所以这里选"明确的失败"，不选"顺滑的丢失"。
    """
    cap = settings.CONVERSATION_MAX_PER_USER
    if cap <= 0:
        return

    used = conversation_repository.count_by_user(db, user.id)
    if used >= cap:
        logger.warning("会话数已达上限 user=%s used=%s cap=%s", user.username, used, cap)
        raise AppException(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "会话数量已达上限（%d 个），请先删除一些旧会话再继续提问" % cap,
        )


# 取历史时"多取几倍再筛"的倍数。无依据的轮次会被丢掉，只取 turns*2 条的话，
# 筛完有效轮数会小于配置值（用户连着问了几次"库里没有"的问题，历史就空了）。
_HISTORY_OVERFETCH = 4


def _has_evidence(msg: Message) -> bool:
    """这条 assistant 消息算不算「有依据的回答」。

    只有有依据的轮次才配当历史里的**事实**：把「知识库中没有找到…」这句固定话术
    以 assistant 身份反复喂回去，模型会顺着那个口径走（越沿用越错），还白占 token。

    判据取 `meta.hit` 而不是"`sources` 为空"——后者会把**寒暄直答**一起卷进来
    （寒暄同样没有 sources，但它不是"没依据"，只是本就不该有依据）。这里取严，
    两种都不进历史，代价只是模型不知道刚才打过招呼。`meta` 缺失（更早的数据）
    按"有依据"处理：宁可多留，也不误删。
    """
    return (msg.meta or {}).get("hit") is not False


def load_history(db: Session, conversation_id: int) -> list[tuple[str, str]]:
    """取最近 `CONVERSATION_MAX_HISTORY_TURNS` 轮**有依据的**问答对，供拼进 Prompt。

    返回 `[("user", …), ("assistant", …), …]`，时间正序。

    做法是"多取 → 从后往前按对聚合 → 丢掉无依据的轮次"，两个细节都不能省：

      · **成对丢**：只丢 assistant 会留下一条没有答案的孤立问题，对模型同样是噪声；
        而 `recent()` 只保证取到最近的 N 条，不保证轮次完整（截断点会落在轮次中间）。
      · **多取**：筛掉无依据的轮次后，只取 turns*2 条会凑不满 turns 轮，
        等于悄悄把用户配的历史轮数打了折。
    """
    turns = max(0, settings.CONVERSATION_MAX_HISTORY_TURNS)
    if turns == 0:
        return []

    rows = message_repository.recent(
        db, conversation_id, limit=turns * 2 * _HISTORY_OVERFETCH
    )
    kept: list[tuple[str, str]] = []
    i = len(rows) - 1
    while i >= 1 and len(kept) < turns * 2:
        question, answer = rows[i - 1], rows[i]
        if question.role == ROLE_USER and answer.role == ROLE_ASSISTANT:
            if _has_evidence(answer):
                # 从头部插入：这样 kept 始终是时间正序（先问先答）
                kept[:0] = [
                    (question.role, question.content),
                    (answer.role, answer.content),
                ]
            i -= 2
        else:
            # 落单的消息（理论上不该出现）：跳过它，保证配对的永远是相邻的问答
            i -= 1
    return kept


def append_turn(
    db: Session,
    conv: Conversation,
    query: str,
    answer: str,
    sources: list | None = None,
    meta: dict | None = None,
    *,
    defer_maintenance: bool = False,
) -> None:
    """把一轮问答写进会话。只 flush，提交由调用方决定。

    ⚠️ **摘要要在裁剪之前做**：`_trim_messages` 会把超出上限的最旧消息**永久删掉**，
    而摘要正是"旧轮次唯一的底"。反过来的顺序会让"还没进摘要就被删"的那几轮彻底消失。

    `defer_maintenance`：**流式路径**把"摘要 + 裁剪"推迟到 `done` 事件之后单独做
    （见本模块的 `maintain_after_stream`）—— 因为摘要是同步的模型调用，
    放在收尾前会让用户"最后一个字吐完了、光标还在转"。
    ⚠️ 要推迟就**两步一起推迟**：只推迟摘要、照常裁剪，就会踩中上面那条
    "还没进摘要就被删"的静默丢数据。
    """
    message_repository.create(db, conv.id, ROLE_USER, query)
    message_repository.create(db, conv.id, ROLE_ASSISTANT, answer, sources=sources, meta=meta)
    conversation_repository.touch(db, conv)
    if defer_maintenance:
        return
    run_maintenance(db, conv)


def run_maintenance(db: Session, conv: Conversation) -> None:
    """摘要 + 裁剪 —— `append_turn` 的"收尾段"，单独拆出来给流式路径复用。

    与 `append_turn` 守的是**同一条顺序不变量**：先摘要、后裁剪（理由见上）。
    """
    maybe_refresh_summary(db, conv)
    _trim_messages(db, conv)


def maintain_after_stream(conversation_id: int) -> None:
    """流式交付完答案**之后**补做收尾：**自己开一个 session**，失败只 warning。

    为什么不跟落库放在一起：`maybe_refresh_summary` 里挂着一次**同步的模型调用**
    （最多 400 token），放在 `done` 之前，用户就得盯着转动的光标等它 —— 非流式时
    它藏在"整段响应"里看不出来，流式下是肉眼可见的停滞。

    为什么不复用请求那个 session：响应体一开始迭代，请求 session 就关了，
    而且 SQLAlchemy 的 Session 不是线程安全的（同步生成器由线程池迭代）。

    顺序仍恪守"先摘要、后裁剪"（`run_maintenance`）—— 裁剪会永久删掉滑出窗口的
    旧消息，摘要是它们唯一的底。

    ⚠️ **两条流式问答路（RAG / Agent）都调这里**："新开 session + 收尾维护"这套动作
    因此在全局只有一份实现。放在本模块而不是 rag_service，是因为它维护的是**会话**
    （`append_turn` / `defer_maintenance` 也在这儿），归属要对得上 —— 否则就会出现
    Agent 侧跨模块去调 RAG 侧私有函数那种别扭的依赖。

    失败方向是安全的：摘要字段没动（下一轮重试）、裁剪晚一轮只是**多留**几条消息。
    客户端在 `done` 一到就断开时这一步会被跳过 —— 同样只是推迟，不丢数据。
    """
    try:
        with db_session.new_session() as db:
            conv = db.get(Conversation, conversation_id)
            if conv is None:
                logger.warning("流式收尾失败：会话已不存在 conv=%s", conversation_id)
                return
            run_maintenance(db, conv)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "流式收尾（摘要/裁剪）失败 conv=%s: %s", conversation_id, str(exc)[:200]
        )


# ==================== 长对话摘要（Phase 12）====================

# 生成摘要那次调用的 max_tokens。**写死不做配置项**：它只是"摘要该多长"的实现细节，
# 真正的约束是 `CONVERSATION_SUMMARY_MAX_CHARS`（拼进提示词的字符预算）。
# 再多一个旋钮，只会让后来人不知道该调哪个。
_SUMMARY_MAX_TOKENS = 400


def summary_section(conv: Conversation) -> str:
    """给提示词用的摘要段 —— 开关关、或还没有摘要时返回空串。

    与记忆段同一个口径：**开关只管"拼不拼进提示词"，不管库里的数据**。
    这样关掉开关时行为与上一阶段逐字一致，也便于做开/关对比。
    """
    if not settings.CONVERSATION_SUMMARY_ENABLED:
        return ""
    return prompt.build_summary_section(
        conv.summary, settings.CONVERSATION_SUMMARY_MAX_CHARS
    )


def _pair_turns(messages: list[Message]) -> list[tuple[str, str]]:
    """按相邻的 (user, assistant) 配成轮次，落单的丢掉。

    与 `load_history` 共享的是**配对规则**（一条没有对应问题的孤立答案对模型是噪声），
    但**刻意不共享「过滤无依据轮次」那一条** —— `load_history` 会丢掉
    `_has_evidence` 为假的轮次，这里不会。两条理由：

      · **形态不同**。`load_history` 是把「知识库中没有找到…」这句**原话**以 assistant
        身份逐条喂回去，模型容易顺着那个口径走（越沿用越错）；而摘要是一条**概述**，
        "问过 X、当时没查到"是对发生过的对话的陈述，不是把拒绝话术当答案，
        诱导强度低得多。
      · **用途不同**。历史的职责是"给模型当前可依据的事实"，宁缺毋滥；
        摘要的职责是"别让早期内容凭空消失"，记下"这个话题被问过"本身有价值
        （以后资料补上了，用户再问时模型知道这不是新话题）。

    ⚠️ 所以这里**不是漏了过滤**。真要把摘要也按 `_has_evidence` 筛，先想清两件事：
    ① 窗口外的无依据轮次会**既不进摘要、又被 `_trim_messages` 删掉** = 永久遗忘；
    ② 若 pending 里的轮次全被筛光，`maybe_refresh_summary` 会因"攒不够 trigger"而
    **永不推进** `summary_upto_id`，每个请求都重复扫同一批（见该函数）。
    """
    turns: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(messages):
        first, second = messages[i], messages[i + 1]
        if first.role == ROLE_USER and second.role == ROLE_ASSISTANT:
            turns.append((first.content, second.content))
            i += 2
        else:
            i += 1
    return turns


def _summarize(conv_id: int, previous: str, new_turns: str) -> str | None:
    """把「已有摘要 + 新增轮次」交给模型合并成一段新摘要。**失败返回 None**。

    失败只 warning、不抛：摘要是"锦上添花"，它挂了不该把这一轮问答也带走
    （与重排失败降级同一个取向）。
    """
    user_text = (
        (f"已有摘要：\n{previous}\n\n" if previous else "")
        + f"新增对话：\n{new_turns}\n\n"
        + "请按系统提示词的要求，输出合并后的摘要。"
    )
    try:
        out = llm.chat(prompt.SUMMARY_SYSTEM, user_text, max_tokens=_SUMMARY_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "对话摘要生成失败（不影响本次问答）conv=%s: %s", conv_id, str(exc)[:200]
        )
        return None
    return (out.get("answer") or "").strip() or None


def maybe_refresh_summary(db: Session, conv: Conversation) -> None:
    """把"已滑出窗口、且还没被摘要"的轮次**增量**压进摘要（失败只 warning）。

    **机会式**而不是定时任务：项目没有调度器；而且摘要在语义上就该"又攒够一批旧轮次时
    顺手做掉"，挂在写入路径上最自然（与 agent_logs 的过期清理同一思路）。

    三条判据，缺一不可：
      · **攒够** `CONVERSATION_SUMMARY_TRIGGER_TURNS` 轮才做 —— 否则每轮都要多调一次模型；
      · **只做增量**：处理 `summary_upto_id` 之后、最近窗口之前的那一段。
        不重头总结的原因很实际：每轮把整段对话重喂一遍，token 会随轮数平方级上涨；
      · **失败不动字段**：`summary` / `summary_upto_id` 原样留着，下一轮还会再试，不丢内容。
    """
    if not settings.CONVERSATION_SUMMARY_ENABLED:
        return

    window_turns = max(0, settings.CONVERSATION_MAX_HISTORY_TURNS)
    if window_turns <= 0:
        # 不带历史时摘要没有意义 —— 它唯一的作用就是替被丢掉的轮次留个底
        return

    window = window_turns * 2
    recent = message_repository.recent(db, conv.id, limit=window)
    if len(recent) < window:
        return  # 还一轮都没滑出窗口
    boundary = recent[0].id  # 窗口内最旧的那条：比它旧才算"滑出去了"

    pending = message_repository.list_between(
        db, conv.id, conv.summary_upto_id or 0, boundary
    )
    turns = _pair_turns(pending)
    if len(turns) < settings.CONVERSATION_SUMMARY_TRIGGER_TURNS:
        return

    new_text = "\n".join(f"问：{q}\n答：{a}" for q, a in turns)
    summary = _summarize(conv.id, conv.summary or "", new_text)
    if summary is None:
        return  # 失败：字段原样留着，下一轮再试

    conv.summary = summary
    conv.summary_upto_id = pending[-1].id
    logger.info(
        "对话摘要已更新 conv=%s 本次覆盖 %s 条 / %s 轮 摘要长度=%s",
        conv.id, len(pending), len(turns), len(summary),
    )


def _trim_messages(db: Session, conv: Conversation) -> None:
    """把会话里的消息裁到 `CONVERSATION_MAX_MESSAGES` 以内（**按整轮**，从最旧的丢）。

    两条口径：

    · **按整轮删，不截半条**。一轮 = 一问一答 = 2 条；删到奇数条会留下"没有对应问题的
      孤立答案"，而历史拼装那边一直在避免这种东西 —— 存库的形态和喂模型的形态
      不该是两套标准。（`excess` 为奇数时多删一条，凑成整轮。）
    · **不可逆，所以每次真删都打 WARNING 并带上条数**。这类"悄悄减少用户数据"的动作
      必须有据可查，否则将来只会看到"我的对话怎么短了"。

    ⚠️ 清理放在 **SAVEPOINT**（`begin_nested`）里，失败只回滚这一小段：
    它跑在"本轮问答已写了一半"的事务里，所以出错的影响面必须被切断 ——
    保存点让"少清几条消息"和"本轮问答"彻底解耦，而不是赌 session 出错后还刚好能用。
    （与 `agent_log_repository.prune_older_than` 同一口径；那边写了它**防不住**什么，
    即整事务级故障如 MySQL 死锁，这里同理。）

    与 `CONVERSATION_MAX_HISTORY_TURNS` 的分工要说清：那个管的是**送进 Prompt 的范围**
    （数据仍在库里，随时回看）；这里管的是**库里留多少**。前者可逆，后者不可逆。
    """
    cap = settings.CONVERSATION_MAX_MESSAGES
    if cap <= 0:
        return

    removed = 0
    try:
        with db.begin_nested():
            total = message_repository.count_by_conversation(db, conv.id)
            excess = total - cap
            if excess > 0:
                if excess % 2:  # 凑成整轮
                    excess += 1
                removed = message_repository.delete_oldest(db, conv.id, excess)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话消息保留清理失败（不影响本次问答）conv=%s: %s", conv.id, str(exc)[:200])
        return
    if removed:
        logger.warning(
            "会话消息超出上限，已删除最旧的 %s 条 conv=%s 上限=%s（按整轮删）",
            removed, conv.id, cap)


def list_conversations(
    db: Session, user: User, limit: int = 50, offset: int = 0
) -> list[tuple[Conversation, int]]:
    """当前用户的会话列表（带消息数），最近活跃排最前。

    `limit` / `offset` 供前端翻页。**这个接口是拿到更早会话的唯一入口**
    （没有"按 id 列出"之类的旁路），所以分页不是锦上添花，是可达性本身。
    """
    return conversation_repository.list_by_user(db, user.id, limit=limit, offset=offset)


def _cites_revoked_kb(sources: list | None, allowed: list[int] | None) -> bool:
    """这条答案有没有引用「此刻已无权访问」的知识库。

    `allowed is None` 表示系统管理员不受限（见 knowledge_service.get_accessible_kb_ids），
    绝不能把它当空集合 —— 那会把管理员的引用全部抹掉。
    """
    if allowed is None:
        return False
    for s in sources or []:
        kb_id = s.get("knowledge_base_id")
        if kb_id is not None and kb_id not in allowed:
            return True
    return False


def load_messages_for_display(
    db: Session,
    user: User,
    conv: Conversation,
    limit: int | None = None,
    before_id: int | None = None,
) -> tuple[list[dict], bool]:
    """会话详情要返回的消息（**已按当前权限脱敏**），返回 `(纯 dict 列表, 是否还有更早的)`。

    ⚠️ 从 Phase 12 起**支持分页**：`limit=None` 才是"全量"（内部与测试用），
    接口一律传 limit —— 长会话一次全量返回会把 `sources`/`meta` 三段 JSON 全吐出去，
    响应又大又慢。分页用 keyset（`before_id`）而不是 offset，理由见
    `message_repository.page_by_conversation`。

    权限的语义在这里被掰清了：**「文档权限」说的是此刻有没有权看，
    而会话记的是当时看到了什么；冲突时不能让历史成为绕开撤销的后门。**
    所以口径取"收资料、不收用户自己的话"：

      · user 消息      → 用户自己敲的字，原样返回
      · assistant 消息 → 只要引用了**任一**此刻无权的库，就**整条**隐藏
        （正文里可能含那个库的内容，无法逐句判断，安全侧取严）
      · 会话标题        → 取自首个问题，也是用户自己的话，保留

    为什么返回 dict 而不是改 ORM 对象上再序列化：直接改 `m.content` / `m.sources`
    会被 SQLAlchemy 记成脏字段，下一次 flush 就**把脱敏结果永久写进数据库** ——
    那是不可逆的数据损坏（原答案再也拿不回来）。
    """
    if limit is None:
        rows = message_repository.list_by_conversation(db, conv.id)
        has_more = False
    else:
        rows, has_more = message_repository.page_by_conversation(
            db, conv.id, limit, before_id
        )

    allowed = knowledge_service.get_accessible_kb_ids(db, user)
    out: list[dict] = []
    for msg in rows:
        if msg.role == ROLE_ASSISTANT and _cites_revoked_kb(msg.sources, allowed):
            out.append({
                "id": msg.id,
                "role": msg.role,
                "content": REDACTED_ANSWER,
                "sources": [],
                # 只留前端渲染分支需要的 source；meta 里其它字段（含 searched_kb_ids
                # 这类库 id 回显）一并丢掉 —— 既然资料已经收回，连"查过哪个库"也不该再给了。
                "meta": {"source": (msg.meta or {}).get("source"), "redacted": True},
                "created_at": msg.created_at,
            })
            continue
        out.append({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "sources": msg.sources,
            "meta": msg.meta,
            "created_at": msg.created_at,
        })
    return out, has_more


def delete_conversation(db: Session, user: User, conversation_id: int) -> None:
    """删会话及其消息。消息走 ORM 级联（见 Conversation.messages）。"""
    conv = get_owned(db, user, conversation_id)
    conversation_repository.delete(db, conv)
    db.commit()
    logger.info("删除会话 user=%s conv_id=%s", user.username, conversation_id)
