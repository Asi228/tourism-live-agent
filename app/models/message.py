"""对话消息表（Phase 9）。

一轮问答落**两条**：一条 user（问题）、一条 assistant（答案）。
把答案也存下来，是因为下一轮要把它当上下文递给模型 —— 只存问题的话，
模型不知道上一轮自己答了什么，"接着刚才那句继续"就无从谈起。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # 只为类型标注，运行时不需要（避免模块级循环引用）
    from app.models.conversation import Conversation
    from app.models.message_feedback import MessageFeedback

# 角色。用常量而不是裸字符串：拼错 "assistant" 这种错很难在测试里被发现
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ALL_ROLES = (ROLE_USER, ROLE_ASSISTANT)


class Message(Base):
    """会话里的一条消息。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        # ondelete="CASCADE" 是生产环境（MySQL）的兜底；测试库 SQLite 走 ORM 级联，
        # 原因见 conversation.py 里的说明。
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="user 或 assistant（见模块常量 ROLE_*）"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="问题原文 / 答案正文")

    # 下面两个都只在 assistant 消息上有值；存 JSON 而不是每种元信息各开一列 ——
    # 它们是**回显给前端看的**，不需要按它们查询或统计（要统计的走 agent_logs）。
    sources: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="引用列表，与答案里的 [n] 编号一一对应"
    )
    meta: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="展示用杂项：answer_mode / hit / reranked / usage / elapsed_ms / model / searched_kb_ids",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    # 删消息时连带删掉它的评价 —— **与 KnowledgeBase.faqs 同源的补漏**：
    # 这张表也只写了数据库侧的 ON DELETE CASCADE，而 SQLite（测试库）不开外键约束，
    # 于是"删会话删到消息"这条路上，反馈会在测试库里变成孤儿、在 MySQL 里被级联删 ——
    # 两边不一致，且测试看不见。
    feedbacks: Mapped[list["MessageFeedback"]] = relationship(
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} conv={self.conversation_id} role={self.role}>"
