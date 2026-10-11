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
from app.models.conversation import TITLE_MAX_LENGTH, Conversation
from app.models.message import ROLE_ASSISTANT, ROLE_USER, Message
from app.models.user import User
from app.repositories import conversation_repository, message_repository
from app.services import knowledge_service

logger = logging.getLogger(__name__)

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
) -> None:
    """把一轮问答写进会话。只 flush，提交由调用方决定。"""
    message_repository.create(db, conv.id, ROLE_USER, query)
    message_repository.create(db, conv.id, ROLE_ASSISTANT, answer, sources=sources, meta=meta)
    conversation_repository.touch(db, conv)
    _trim_messages(db, conv)


def _trim_messages(db: Session, conv: Conversation) -> None:
    """把会话里的消息裁到 `CONVERSATION_MAX_MESSAGES` 以内（**按整轮**，从最旧的丢）。

    两条口径：

    · **按整轮删，不截半条**。一轮 = 一问一答 = 2 条；删到奇数条会留下"没有对应问题的
      孤立答案"，而历史拼装那边一直在避免这种东西 —— 存库的形态和喂模型的形态
      不该是两套标准。（`excess` 为奇数时多删一条，凑成整轮。）
    · **不可逆，所以每次真删都打 WARNING 并带上条数**。这类"悄悄减少用户数据"的动作
      必须有据可查，否则将来只会看到"我的对话怎么短了"。

    ⚠️ 失败只记 warning、**不 rollback**：它跑在"本轮问答已写了一半"的事务里，
    rollback 会把刚写好的这一轮一起丢掉 —— 那比少清理几条消息严重得多。

    与 `CONVERSATION_MAX_HISTORY_TURNS` 的分工要说清：那个管的是**送进 Prompt 的范围**
    （数据仍在库里，随时回看）；这里管的是**库里留多少**。前者可逆，后者不可逆。
    """
    cap = settings.CONVERSATION_MAX_MESSAGES
    if cap <= 0:
        return

    try:
        total = message_repository.count_by_conversation(db, conv.id)
        excess = total - cap
        if excess <= 0:
            return
        if excess % 2:  # 凑成整轮
            excess += 1
        removed = message_repository.delete_oldest(db, conv.id, excess)
        if removed:
            logger.warning(
                "会话消息超出上限，已删除最旧的 %s 条 conv=%s 上限=%s（按整轮删）",
                removed, conv.id, cap,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话消息保留清理失败（不影响本次问答）conv=%s: %s", conv.id, str(exc)[:200])


def list_conversations(
    db: Session, user: User, limit: int = 50, offset: int = 0
) -> list[tuple[Conversation, int]]:
    """当前用户的会话列表（带消息数），最近活跃排最前。

    `limit` / `offset` 供前端翻页。**这个接口是拿到更早会话的唯一入口**
    （没有"按 id 列出"之类的旁路），所以分页不是锦上添花，是可达性本身。
    """
    return conversation_repository.list_by_user(db, user.id, limit=limit, offset=offset)


def get_messages(db: Session, user: User, conversation_id: int) -> list[Message]:
    """读一个会话的全部消息（校验归属）。"""
    conv = get_owned(db, user, conversation_id)
    return message_repository.list_by_conversation(db, conv.id)


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


def load_messages_for_display(db: Session, user: User, conv: Conversation) -> list[dict]:
    """会话详情要返回的消息（**已按当前权限脱敏**），返回纯 dict。

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
    allowed = knowledge_service.get_accessible_kb_ids(db, user)
    out: list[dict] = []
    for msg in message_repository.list_by_conversation(db, conv.id):
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
    return out


def delete_conversation(db: Session, user: User, conversation_id: int) -> None:
    """删会话及其消息。消息走 ORM 级联（见 Conversation.messages）。"""
    conv = get_owned(db, user, conversation_id)
    conversation_repository.delete(db, conv)
    db.commit()
    logger.info("删除会话 user=%s conv_id=%s", user.username, conversation_id)
