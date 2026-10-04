"""Agent 执行记录表。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentLog(Base):
    """一次 Agent 问答的**决策过程**记录。

    为什么值得单独一张表：Agent 和普通 RAG 的唯一区别就是"它自己决定怎么走"
    —— 而这个过程默认是**不可见**的。多绕了一圈、工具白调了一次、语义没变，
    从最终答案上完全看不出来，只能靠翻应用日志猜。

    落库之后这些问题就变成可查询的：
      · 这次走了几步、每步耗时多少（steps）
      · 检索了几轮（iterations）—— 轮数明显偏高说明提示词或工具描述需要调
      · 一共烧了多少 token（total_tokens）—— 和纯 RAG 对比就知道 Agent 值不值
      · 有没有失败（error_msg）

    它和 messages 表不同：messages 存的是**对话内容**（留给 Phase 9 的会话记忆），
    这里存的是**执行轨迹**，两者关注的不是一回事。
    """

    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False, comment="发起人"
    )

    query: Mapped[str] = mapped_column(String(1000), nullable=False, comment="用户问题")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最终答案（便于复盘）")
    hit: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
        comment="是否检索到了资料",
    )
    source_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="引用了多少条资料"
    )

    iterations: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="实际检索轮数"
    )
    steps: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment='每步轨迹，如 [{"node":"retrieve","ms":820,"iteration":1}]'
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="本次消耗的 token 合计"
    )
    elapsed_ms: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="总耗时（毫秒）"
    )

    error_msg: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="失败原因（不含堆栈）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentLog id={self.id} user={self.user_id} iterations={self.iterations}>"
