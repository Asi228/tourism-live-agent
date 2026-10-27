"""RPA 与模拟平台数据访问；事务由 service/worker 管理。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.rpa import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    MonitoringPlan,
    MockPlatformSku,
    RpaApproval,
    RpaTask,
    RpaTaskStep,
)


def create_task(db: Session, **fields) -> RpaTask:
    row = RpaTask(**fields)
    db.add(row)
    db.flush()
    return row


def get_task_by_idempotency_key(db: Session, key: str) -> RpaTask | None:
    return db.scalar(select(RpaTask).where(RpaTask.idempotency_key == key))


def get_task(db: Session, task_id: int) -> RpaTask | None:
    return db.scalar(
        select(RpaTask)
        .options(selectinload(RpaTask.steps))
        .where(RpaTask.id == task_id)
    )


def list_tasks(db: Session, owner_id: int | None = None) -> list[RpaTask]:
    stmt = select(RpaTask).options(selectinload(RpaTask.steps))
    if owner_id is not None:
        stmt = stmt.where(RpaTask.requested_by == owner_id)
    return list(db.scalars(stmt.order_by(RpaTask.id.desc()).limit(100)))


def list_tasks_since(
    db: Session, *, since: datetime, owner_id: int | None = None
) -> list[RpaTask]:
    stmt = select(RpaTask).where(RpaTask.created_at >= since)
    if owner_id is not None:
        stmt = stmt.where(RpaTask.requested_by == owner_id)
    return list(db.scalars(stmt.order_by(RpaTask.id.desc())))


def list_tasks_for_session(db: Session, session_id: int) -> list[RpaTask]:
    return list(
        db.scalars(
            select(RpaTask)
            .options(selectinload(RpaTask.steps))
            .where(RpaTask.session_id == session_id)
            .order_by(RpaTask.id.desc())
            .limit(20)
        )
    )


def list_recap_tasks_for_session(db: Session, session_id: int) -> list[RpaTask]:
    """复盘使用完整任务记录；步骤证据仍通过任务详情按需读取。"""
    return list(
        db.scalars(
            select(RpaTask)
            .where(RpaTask.session_id == session_id)
            .order_by(RpaTask.id.desc())
        )
    )


def create_step(db: Session, **fields) -> RpaTaskStep:
    row = RpaTaskStep(**fields)
    db.add(row)
    db.flush()
    return row


def list_mock_skus(db: Session) -> list[MockPlatformSku]:
    return list(db.scalars(select(MockPlatformSku).order_by(MockPlatformSku.id)))


def get_mock_sku(db: Session, row_id: int) -> MockPlatformSku | None:
    return db.get(MockPlatformSku, row_id)


def get_mock_sku_by_code(db: Session, sku_code: str) -> MockPlatformSku | None:
    return db.scalar(select(MockPlatformSku).where(MockPlatformSku.sku_code == sku_code))


def create_mock_sku(db: Session, **fields) -> MockPlatformSku:
    row = MockPlatformSku(**fields)
    db.add(row)
    db.flush()
    return row


# ---------- 审批单 ----------


def create_approval(db: Session, **fields) -> RpaApproval:
    row = RpaApproval(**fields)
    db.add(row)
    db.flush()
    return row


def get_approval(db: Session, approval_id: int) -> RpaApproval | None:
    return db.get(RpaApproval, approval_id)


def list_approvals(db: Session, owner_id: int | None = None) -> list[RpaApproval]:
    stmt = select(RpaApproval)
    if owner_id is not None:
        stmt = stmt.where(RpaApproval.requested_by == owner_id)
    return list(db.scalars(stmt.order_by(RpaApproval.id.desc()).limit(100)))


def list_approvals_since(
    db: Session, *, since: datetime, owner_id: int | None = None
) -> list[RpaApproval]:
    stmt = select(RpaApproval).where(RpaApproval.created_at >= since)
    if owner_id is not None:
        stmt = stmt.where(RpaApproval.requested_by == owner_id)
    return list(db.scalars(stmt.order_by(RpaApproval.id.desc())))


def list_approvals_for_session(db: Session, session_id: int) -> list[RpaApproval]:
    return list(
        db.scalars(
            select(RpaApproval)
            .where(RpaApproval.session_id == session_id)
            .order_by(RpaApproval.id.desc())
            .limit(20)
        )
    )


def list_recap_approvals_for_session(
    db: Session, session_id: int
) -> list[RpaApproval]:
    return list(
        db.scalars(
            select(RpaApproval)
            .where(RpaApproval.session_id == session_id)
            .order_by(RpaApproval.id.desc())
        )
    )


def find_open_approval(
    db: Session, *, session_id: int, sku_id: int, task_type: str
) -> RpaApproval | None:
    """同一对象、同一操作类型是否已有"待处理/已批准但未执行完"的申请。

    去重口径只看 pending / approved —— 已 executed/failed/rejected 的可以再申请
    （改完发现价格又错了，允许再来一次）。
    """
    return db.scalar(
        select(RpaApproval).where(
            RpaApproval.session_id == session_id,
            RpaApproval.sku_id == sku_id,
            RpaApproval.task_type == task_type,
            RpaApproval.status.in_((APPROVAL_PENDING, APPROVAL_APPROVED)),
        )
    )


# ---------- 定时监控计划 ----------


def get_monitoring_plan_by_session(
    db: Session, session_id: int
) -> MonitoringPlan | None:
    return db.scalar(
        select(MonitoringPlan).where(MonitoringPlan.session_id == session_id)
    )


def list_monitoring_plans(
    db: Session, owner_id: int | None = None
) -> list[MonitoringPlan]:
    stmt = select(MonitoringPlan)
    if owner_id is not None:
        # 可见范围跟直播场次负责人走。管理员代运营配置的计划也应让负责人看到，
        # created_by 只承担审计含义，不能拿来判断业务归属。
        stmt = stmt.where(MonitoringPlan.session.has(owner_id=owner_id))
    return list(db.scalars(stmt.order_by(MonitoringPlan.id.desc())))


def create_monitoring_plan(db: Session, **fields) -> MonitoringPlan:
    row = MonitoringPlan(**fields)
    db.add(row)
    db.flush()
    return row


def list_due_monitoring_plans(db: Session, now: datetime) -> list[MonitoringPlan]:
    return list(
        db.scalars(
            select(MonitoringPlan)
            .where(MonitoringPlan.enabled.is_(True), MonitoringPlan.next_run_at <= now)
            .order_by(MonitoringPlan.next_run_at, MonitoringPlan.id)
            .with_for_update(skip_locked=True)
        )
    )
