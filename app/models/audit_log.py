"""管理操作审计表。

为什么需要它：改角色、授权、删库这些操作**会改变"谁能看到什么"** ——
一旦出问题（某人忽然能看财务库了），事后必须能回答三个问题：
谁干的、什么时候、把什么改成了什么。

和 `agent_logs` 的分工说清楚：
  · `agent_logs`  = **问答**的执行轨迹（几轮检索、烧了多少 token）
  · `audit_logs`  = **管理动作**的痕迹（谁把谁从 employee 改成 admin）

刻意**只记管理类动作**，不做全量请求日志：请求日志该由 web 服务器 / 网关承担，
写进业务库只会变成一张没人看的巨表，还会和"删用户"互相纠缠。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 动作名用 `模块.动作` 的形式，方便按前缀过滤（如 `user.` 看所有用户管理动作）。
ACTION_ROLE_CHANGE = "user.role_change"
ACTION_USER_CREATE = "user.create"
ACTION_USER_STATUS = "user.status_change"
ACTION_USER_PASSWORD_RESET = "user.password_reset"
ACTION_MEMBER_GRANT = "kb.member_grant"
ACTION_MEMBER_REVOKE = "kb.member_revoke"
ACTION_KB_CREATE = "kb.create"
ACTION_KB_DELETE = "kb.delete"
ACTION_KB_VISIBILITY = "kb.visibility_change"

ALL_ACTIONS = (
    ACTION_ROLE_CHANGE,
    ACTION_USER_CREATE,
    ACTION_USER_STATUS,
    ACTION_USER_PASSWORD_RESET,
    ACTION_MEMBER_GRANT,
    ACTION_MEMBER_REVOKE,
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_KB_VISIBILITY,
)


class AuditLog(Base):
    """一条管理操作记录。

    ⚠️ `actor_id` **没有外键** —— 与 `user_memories.source_conversation_id` 同一个理由：
    加外键会让"删除一个用户"直接失败（他留下的审计记录会被牵连），
    而审计恰恰是**最不该因为业务数据被删而消失**的东西。代价是这一列可能悬空，
    查询时要容忍"操作者已不存在"。

    ## 覆盖边界（写清楚，免得有人以为这张表是全的）

    **覆盖**——能改变"谁能看到什么"的权限与可见性变更：
    **开通账号、停用 / 启用账号、重置密码**、改角色、知识库授权 / 撤销、建库、删库、
    **改为公开 / 私有**。

    **不覆盖**——两类刻意不记的：
      · **内容级**：删文档、单文档 / 整库向量化。它们改变的是"能检索到什么"，
        但由 `documents` 表自身记录归属（谁传的、什么时候删），
        且高频（一次向量化几百条向量）—— 记进来只会淹没真正的权限变更。
      · **元数据级**：改知识库名称 / 描述。不影响"谁能看到什么"。

    要做成"全量操作日志"是另一个东西（该由 web 服务器 / 网关承担），
    塞进业务库只会变成一张没人看的巨表。

    ⚠️ **这张表永不清理**。别照 `agent_logs` 的保留策略顺手给它加一个 prune ——
    审计能被定期删除，就不叫审计了（`agent_logs` 是运行数据，可以过期；
    这里记的是"谁改了权限"，它的价值随时间**增长**而不是衰减）。
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    actor_id: Mapped[int] = mapped_column(
        Integer, index=True, nullable=False, comment="操作者 user_id（刻意无外键，见类说明）"
    )
    actor_name: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="操作者用户名**快照** —— 用户被删/改名后，这条审计仍读得懂",
    )

    action: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, comment="动作名，见模块常量"
    )
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="对象类型：user / knowledge_base"
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="对象 ID")
    target_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="对象名称快照（同样为了事后可读）"
    )

    detail: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment='变更内容，如 {"from":"employee","to":"admin"}'
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.target_type}={self.target_id} by {self.actor_id}>"
