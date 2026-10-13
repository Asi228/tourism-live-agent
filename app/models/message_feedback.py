"""答案反馈表（Bad Case 闭环）。

**为什么要有这张表**：RAG 的效果只能靠真实使用来验证 —— 检索命中了不代表答对了。
没有反馈通道，就永远只是"看着像能用"；有了它，才形成
「使用 → 反馈 → 补文档 / 调切片 / 改 Prompt → 效果提升」的闭环。

设计上的三条取舍：

1. **唯一约束 `(message_id, user_id)`，重复提交是更新而不是报错。**
   用户改主意（先点了没用、看清楚后又觉得有用）是常态，
   报"你已经评价过了"只会逼他不改。upsert 才是对的语义。

2. **`message_id` 加外键并 `ON DELETE CASCADE`。**
   反馈是消息的附属品：消息被裁剪掉之后，它对应的反馈没有意义。
   这与 `user_memories.source_conversation_id`（**刻意无外键**）正好相反 ——
   那边是"记忆比会话活得久"，这边是"反馈跟消息一起走"，两者都对，取决于谁依赖谁。

3. **只对 assistant 消息评分**，由 service 层校验（user 消息是用户自己写的，
   给自己的提问打"答案错误"没有意义）。

⚠️ 归属校验走"消息 → 会话 → get_owned"，别人的消息一律 404
（403 等于白送"这个 id 存在"），口径与会话、记忆一致。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 评分。用常量而不是裸字符串：拼错 "up" 这类错误在测试里很难被发现
RATING_UP = "up"
RATING_DOWN = "down"
ALL_RATINGS = (RATING_UP, RATING_DOWN)

# 差评原因。前端下拉就是这一份，改这里两边一起改
REASON_WRONG = "答案错误"
REASON_IRRELEVANT = "与问题无关"
REASON_OFF_TOPIC = "文不对题"
REASON_OUTDATED = "信息过时"
REASON_OTHER = "其他"
ALL_REASONS = (
    REASON_WRONG,
    REASON_IRRELEVANT,
    REASON_OFF_TOPIC,
    REASON_OUTDATED,
    REASON_OTHER,
)

COMMENT_MAX_LENGTH = 500


class MessageFeedback(Base):
    """一条消息上、某个用户的一次评价。"""

    __tablename__ = "message_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="被评价的消息（消息被裁剪删除时，反馈一起走）",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="评价人"
    )
    rating: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="up / down（见模块常量 RATING_*）"
    )
    reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="差评原因（见模块常量 REASON_*）；好评为 null"
    )
    comment: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="补充备注，最多 500 字"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 改主意也算一次更新 —— 列表按它倒序能看出"最近改过哪些"
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
    )

    def __repr__(self) -> str:
        return (
            f"<MessageFeedback msg={self.message_id} user={self.user_id} "
            f"rating={self.rating}>"
        )
