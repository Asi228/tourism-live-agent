"""会话的数据访问。

口径与其余 repository 一致：**只 flush，不 commit** —— 事务边界归 service 层。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.message import Message

logger = logging.getLogger(__name__)


def get_by_id(db: Session, conversation_id: int) -> Conversation | None:
    return db.get(Conversation, conversation_id)


def list_by_user(
    db: Session, user_id: int, limit: int = 50, offset: int = 0
) -> list[tuple[Conversation, int]]:
    """列出某用户的会话，**顺带把消息数一起查出来**。

    返回 `[(Conversation, message_count), …]`。用一条 join + group by 拿到计数，
    而不是"先查会话、再逐个 count"—— 后者就是典型的 N+1。
    列表也要按最近活跃排序，所以 updated_at 建了索引。

    `offset` 是分页用的：**只有 limit 的话，第 N 条之后的会话在界面上永远翻不到**
    （接口没有任何别的入口能列出更早的会话），所以这里必须能跳过前 N 条。
    """
    stmt = (
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
        .group_by(Conversation.id)
        # 次键加 id：MySQL 的 DATETIME 默认只到**秒**，同一秒内开的两段对话
        # updated_at 完全相同，只按时间排会得到不确定的顺序（测试里实测踩到过）。
        # id 单调递增，正好当稳定的第二排序键。
        #
        # ⚠️ 排序必须**稳定**，否则 offset 分页会漏条/重条：同一秒内的两条若顺序不定，
        #    第 1 页和第 2 页的边界就可能抽风。这也是次键必须存在的原因。
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def count_by_user(db: Session, user_id: int) -> int:
    """某个用户现有多少个会话（数据保留的配额判断用）。

    注意它和 `list_by_user` 不是一回事：那个是**带分页的展示查询**，
    这里要的是**总数**，不能被 limit 影响。
    """
    return db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    ) or 0


def create(db: Session, user_id: int, title: str) -> Conversation:
    conv = Conversation(user_id=user_id, title=title)
    db.add(conv)
    db.flush()          # 只为拿到自增 id；提交由 service 决定
    return conv


def touch(db: Session, conv: Conversation) -> None:
    """把 updated_at 推到当前时间（列表排序用）。

    不依赖 `onupdate=func.now()`：那个只在**该行其它字段**发生变化时才触发，
    我们追加消息时并没有改 conversation 的任何列 —— 不手动碰一下，会话就永远
    按创建时间排序，刚聊过的沉到底下。
    """
    conv.updated_at = func.now()
    db.flush()


def delete(db: Session, conv: Conversation) -> None:
    """删会话。消息由 ORM 级联删除（见 Conversation.messages 的 cascade）。"""
    db.delete(conv)
    db.flush()
