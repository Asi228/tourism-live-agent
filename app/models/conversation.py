"""会话表（Phase 9）。

它和 `agent_logs` 的分工：
  · `agent_logs` 记的是**执行过程**（走了几步、烧了多少 token）—— 给排障和成本分析看
  · `conversations` / `messages` 记的是**对话内容** —— 给"接着上一句继续问"用

**为什么不存 kb_ids**：检索范围是"这一问想查哪些库"的意图，用户随时可能改
（这次只查员工手册、下次查全部）。把它锁在会话上反而会让用户改了选择却不生效。
所以范围仍然每次请求带，会话只负责记"聊了什么"。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # 只为类型标注，运行时不需要（避免模块级循环引用）
    from app.models.message import Message

# 会话标题的截断长度。取首个问题前 N 个字做标题 —— 比"新会话"好认。
TITLE_MAX_LENGTH = 60


class Conversation(Base):
    """一次连续对话。用户没说"新建"时，由后端在首次提问时隐式创建。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="会话归属，只有本人能看"
    )
    title: Mapped[str] = mapped_column(
        String(TITLE_MAX_LENGTH), nullable=False, comment="取自首个问题，便于在列表里认出来"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 每次追加消息都会更新 —— 列表按它倒序，最近聊的排最前
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True, nullable=False
    )

    # ⚠️ 用 ORM 级联而不是只靠数据库级联：**测试库是 SQLite，而 SQLite 默认不启用外键约束**
    #    （PRAGMA foreign_keys=OFF），光靠 DB 级联的话测试里删会话不会删消息，
    #    问题会被测试环境掩盖。message 那边的 FK 也写了 ondelete="CASCADE" 作为生产兜底。
    #    不用 lazy="selectin"：列表页只要标题，把整段对话都拉出来纯属浪费。
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} user={self.user_id} title={self.title[:12]!r}>"
