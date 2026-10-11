"""长期记忆表（Phase 11）。

与**会话记忆**（`conversations` / `messages`）的分工，一句话说清：

  · `messages` 记的是**这一次对话里说过什么** —— 只影响同一段会话的最近 N 轮；
  · `user_memories` 记的是**换一段会话仍然成立的事实** —— "我在财务部"、
    "我们季度报销的截止日是每月 25 号"。它的价值恰恰在于跨会话。

两处刻意的设计：

1. **归属挂 `user_id`，不挂会话** —— 记忆的意义就是"换个会话还记得"，
   挂在会话上就退化成普通的上下文了。

2. ⚠️ `source_conversation_id` **刻意不加外键**。记忆的生命周期比会话长：
   删掉某段对话不该把它提炼出的记忆一起带走；而加了 FK（默认 RESTRICT）
   反而会让"删除会话"直接失败，把一个分类操作变成 500。
   这一列只是追溯用的线索，不承担一致性。

   类型与 `conversations.id` **保持一致（`Integer`）**：跨表的软引用也用同一个宽度，
   免得后来人对着两种整数类型猜"是不是故意的"。代价是没有外键保护、值可能悬空。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 单条记忆的字符上限。超了**直接拒绝**（40000），不静默截断 ——
# 用户以为记住了一整段，实际只存了前半段，是最难排查的那种"记错了"。
CONTENT_MAX_LENGTH = 2000


class UserMemory(Base):
    """一条长期记忆。由用户**显式**写入（见 /api/memories）。"""

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="归属用户，只有本人能读写"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆正文")
    source_conversation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="产生它的会话 id（仅作追溯、无外键；**可能指向已删除的会话**）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 拼进提示词时按它倒序（最近说的最相关），所以有索引
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True, nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserMemory id={self.id} user={self.user_id} {self.content[:12]!r}>"
