"""RPA 任务、执行步骤和开发期模拟平台数据。"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.live_commerce import InspectionResult, LiveSession, ProductSku
    from app.models.user import User


TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_QUEUE_FAILED = "queue_failed"

# 任务类型：采集是只读动作；修复是**写**动作，必须先有审批单
TASK_COLLECT = "collect_platform_data"
TASK_PRICE_FIX = "price_fix"
TASK_DELIST = "delist"

# 审批状态机：pending → approved | rejected ；approved 执行完 → executed | failed
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXECUTED = "executed"
APPROVAL_FAILED = "failed"


class RpaTask(Base):
    __tablename__ = "rpa_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=TASK_QUEUED, server_default=TASK_QUEUED,
        index=True, nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped["LiveSession"] = relationship(lazy="joined")
    requester: Mapped["User"] = relationship(lazy="joined")
    steps: Mapped[list["RpaTaskStep"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="RpaTaskStep.id"
    )


class RpaTaskStep(Base):
    __tablename__ = "rpa_task_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("rpa_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    step_code: Mapped[str] = mapped_column(String(64), nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped[RpaTask] = relationship(back_populates="steps")


class MonitoringPlan(Base):
    """直播场次的只读定时采集计划；调度器只负责创建 RPA 采集任务。"""

    __tablename__ = "monitoring_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), unique=True, index=True,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True, nullable=False
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("rpa_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped["LiveSession"] = relationship(lazy="joined")
    creator: Mapped["User"] = relationship(lazy="joined")
    last_task: Mapped["RpaTask | None"] = relationship(lazy="joined")


class MockPlatformSku(Base):
    """模拟外部平台的数据源；RPA 只能通过网页读取它。"""

    __tablename__ = "mock_platform_skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    platform_status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    list_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    live_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    promotion_starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promotion_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stackable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RpaApproval(Base):
    """高风险操作审批单（改价 / 下架）—— **一次审批 = 一次可执行的授权**。

    为什么审批单要自带"预期值 + 幂等键"：
      · 预期值：执行前后都拿它比对 —— 防"批的是 99 元、实际写成 89 元"；
      · 幂等键：审批通过后生成的 RPA 任务用它做唯一约束 ——
        重复点"通过"、worker 重试都不会产生第二次改价。
    审批本身是**单级**的（场次负责人或管理员），多级审批是后续扩展点；
    但"申请人不能批自己单"这条**现在就强制**（四眼原则）。
    """

    __tablename__ = "rpa_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("product_skus.id"), index=True, nullable=False
    )
    expected_value: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="目标状态（执行后按它验证），如 {'live_price': '99.00'}"
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=APPROVAL_PENDING, server_default=APPROVAL_PENDING,
        index=True, nullable=False,
    )
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="批准备注 / 驳回理由"
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("rpa_tasks.id"), nullable=True, comment="通过后生成的执行任务"
    )
    source_inspection_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspection_results.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        comment="触发本申请的巡检风险",
    )
    source_snapshot: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="申请时的风险与证据快照"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped["LiveSession"] = relationship(lazy="joined")
    sku: Mapped["ProductSku"] = relationship(lazy="joined")
    task: Mapped["RpaTask | None"] = relationship(lazy="joined")
    source_inspection_result: Mapped["InspectionResult | None"] = relationship(lazy="joined")
    # 两个外键都指向 users，必须显式指定，否则 SQLAlchemy 无法判断关系用哪个键
    requester: Mapped["User"] = relationship(foreign_keys=[requested_by], lazy="joined")
    approver: Mapped["User | None"] = relationship(foreign_keys=[approved_by], lazy="joined")
