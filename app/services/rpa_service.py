"""RPA 任务创建、Redis 入队和模拟平台数据维护。"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.redis_client import get_client
from app.core.response import ErrorCode
from app.models.rpa import (
    APPROVAL_APPROVED,
    APPROVAL_EXECUTED,
    APPROVAL_FAILED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TASK_COLLECT,
    TASK_DELIST,
    TASK_FAILED,
    TASK_PRICE_FIX,
    TASK_QUEUE_FAILED,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    MockPlatformSku,
    RpaApproval,
    RpaTask,
)
from app.models.user import User
from app.repositories import live_commerce_repository as commerce_repo
from app.repositories import rpa_repository as repo
from app.services import live_commerce_service

RPA_QUEUE_KEY = "enterprise-agent:rpa:tasks"
logger = logging.getLogger(__name__)


def _require_admin(user: User) -> None:
    if user.role.code != "admin":
        raise AppException(ErrorCode.FORBIDDEN, "需要管理员权限")


def _get_task_for_user(db: Session, user: User, task_id: int) -> RpaTask:
    row = repo.get_task(db, task_id)
    if row is None or (user.role.code != "admin" and row.requested_by != user.id):
        raise AppException(ErrorCode.NOT_FOUND, "自动化任务不存在或没有访问权限")
    return row


def _push_task(task_id: int) -> None:
    get_client().rpush(RPA_QUEUE_KEY, str(task_id))


def _is_demo_sandbox_user(user: User) -> bool:
    return settings.DEMO_READ_ONLY and user.username == settings.DEMO_READ_ONLY_USERNAME


def _complete_demo_task(
    db: Session, task: RpaTask, *, approval: RpaApproval | None = None
) -> RpaTask:
    """在轻量公网沙箱中模拟外部平台执行，并保留完整步骤和核验结果。

    正式环境仍由 Redis + 独立 Playwright worker 执行。Render 免费实例没有常驻
    worker 和浏览器进程，因此演示账号走同一任务状态机，但用本地模拟平台即时收尾。
    """
    now = datetime.now()
    task.status = TASK_RUNNING
    task.started_at = now
    task.attempt_count = (task.attempt_count or 0) + 1
    db.commit()

    if task.task_type == TASK_COLLECT:
        sku_codes = list((task.payload or {}).get("sku_codes") or [])
        task.result = {
            "collected": [{"sku_code": code, "source": "demo_mock_platform"} for code in sku_codes],
            "missing_sku_codes": [],
            "inspection": {"status": "completed", "conclusion": "ready"},
            "verified": True,
        }
        steps = (
            ("login", "登录模拟平台", "演示平台登录成功"),
            ("collect", "采集商品数据", f"已采集 {len(sku_codes)} 个 SKU 的价格、库存和活动"),
            ("persist", "保存业务快照", "结构化快照已保存"),
            ("inspect", "执行开播检查", "价格、库存、活动和商品状态检查完成"),
        )
    else:
        payload = task.payload or {}
        sku_code = str(payload.get("sku_code") or "")
        expected = dict(payload.get("expected") or {})
        platform_sku = repo.get_mock_sku_by_code(db, sku_code)
        before = {}
        if platform_sku is not None:
            before = {
                "live_price": str(platform_sku.live_price) if platform_sku.live_price is not None else None,
                "platform_status": platform_sku.platform_status,
            }
            if "live_price" in expected:
                platform_sku.live_price = Decimal(str(expected["live_price"]))
            if "platform_status" in expected:
                platform_sku.platform_status = str(expected["platform_status"])
        task.result = {
            "sku_code": sku_code,
            "before": before,
            "actual": expected,
            "verified": True,
            "source": "demo_mock_platform",
        }
        steps = (
            ("login", "登录模拟平台", "演示平台登录成功"),
            ("locate", "定位目标商品", f"已定位 {sku_code}"),
            ("execute", "执行审批操作", "目标值已写入模拟平台"),
            ("verify", "回读核验结果", "平台回读结果与审批目标一致"),
        )

    for index, (code, name, message) in enumerate(steps):
        started = now + timedelta(seconds=index)
        repo.create_step(
            db,
            task_id=task.id,
            step_code=code,
            step_name=name,
            status=TASK_SUCCEEDED,
            message=message,
            screenshot_path=None,
            started_at=started,
            completed_at=started + timedelta(milliseconds=500),
        )
    task.status = TASK_SUCCEEDED
    task.completed_at = now + timedelta(seconds=len(steps))
    task.error_message = None
    if approval is not None:
        approval.status = APPROVAL_EXECUTED
    db.commit()
    return repo.get_task(db, task.id)


def list_monitoring_plans(db: Session, user: User):
    return repo.list_monitoring_plans(
        db, None if user.role.code == "admin" else user.id
    )


def upsert_monitoring_plan(
    db: Session,
    user: User,
    *,
    session_id: int,
    enabled: bool,
    interval_minutes: int,
):
    session = live_commerce_service.get_session(db, user, session_id)
    if not session.items:
        raise AppException(ErrorCode.PARAM_ERROR, "请先为直播场次添加选品")
    now = datetime.now()
    row = repo.get_monitoring_plan_by_session(db, session_id)
    if row is None:
        row = repo.create_monitoring_plan(
            db,
            session_id=session_id,
            enabled=enabled,
            interval_minutes=interval_minutes,
            next_run_at=now + timedelta(minutes=interval_minutes),
            created_by=user.id,
        )
    else:
        was_enabled = row.enabled
        interval_changed = row.interval_minutes != interval_minutes
        row.enabled = enabled
        row.interval_minutes = interval_minutes
        if enabled and (not was_enabled or interval_changed):
            row.next_run_at = now + timedelta(minutes=interval_minutes)
    db.commit()
    db.refresh(row)
    return row


def dispatch_due_monitoring_plans(db: Session, *, now: datetime | None = None) -> list[int]:
    """锁定到期计划、幂等创建只读采集任务，并推入 Redis 队列。"""
    current = now or datetime.now()
    plans = repo.list_due_monitoring_plans(db, current)
    tasks = []
    for plan in plans:
        scheduled_for = plan.next_run_at
        if plan.session.status == "finished":
            plan.enabled = False
            continue
        key = uuid5(
            NAMESPACE_URL, f"monitoring-plan:{plan.id}:{scheduled_for.isoformat()}"
        ).hex
        task = repo.get_task_by_idempotency_key(db, key)
        if task is None:
            task = repo.create_task(
                db,
                session_id=plan.session_id,
                task_type=TASK_COLLECT,
                status=TASK_QUEUED,
                idempotency_key=key,
                # 任务与指标归属于场次负责人；created_by 只记录谁配置了计划。
                requested_by=plan.session.owner_id,
                payload={
                    "session_id": plan.session_id,
                    "sku_codes": [item.sku.sku_code for item in plan.session.items],
                    "monitoring_plan_id": plan.id,
                    "scheduled_for": scheduled_for.isoformat(),
                },
            )
        plan.last_run_at = current
        plan.last_task_id = task.id
        next_run = scheduled_for
        while next_run <= current:
            next_run += timedelta(minutes=plan.interval_minutes)
        plan.next_run_at = next_run
        tasks.append(task)
    db.commit()

    queued_ids = []
    for task in tasks:
        try:
            _push_task(task.id)
            queued_ids.append(task.id)
        except Exception as exc:
            task.status = TASK_QUEUE_FAILED
            task.error_message = f"Redis 入队失败：{exc}"[:500]
            db.commit()
            logger.warning("定时监控任务入队失败 task=%s: %s", task.id, exc)
    return queued_ids


def create_collection_task(
    db: Session, user: User, *, session_id: int
) -> RpaTask:
    session = live_commerce_service.get_session(db, user, session_id)
    if not session.items:
        raise AppException(ErrorCode.PARAM_ERROR, "请先为直播场次添加选品")
    row = repo.create_task(
        db,
        session_id=session.id,
        task_type="collect_platform_data",
        status=TASK_QUEUED,
        idempotency_key=uuid4().hex,
        requested_by=user.id,
        payload={"session_id": session.id, "sku_codes": [item.sku.sku_code for item in session.items]},
    )
    db.commit()
    if _is_demo_sandbox_user(user):
        return _complete_demo_task(db, row)
    try:
        _push_task(row.id)
    except Exception as exc:  # Redis 故障不丢任务，保留为可重新入队状态。
        row.status = TASK_QUEUE_FAILED
        row.error_message = f"Redis 入队失败: {str(exc)[:300]}"
        db.commit()
    return repo.get_task(db, row.id)


def requeue_task(db: Session, user: User, *, task_id: int) -> RpaTask:
    row = _get_task_for_user(db, user, task_id)
    # queued 已经在 Redis 中，再次 RPUSH 只会制造重复消息；只有明确失败的任务
    # 才允许人工重新入队。worker 仍会对终态做第二层幂等保护。
    if row.status not in {TASK_QUEUE_FAILED, TASK_FAILED}:
        raise AppException(ErrorCode.PARAM_ERROR, "当前任务状态不能重新入队")
    try:
        _push_task(row.id)
    except Exception as exc:
        row.status = TASK_QUEUE_FAILED
        row.error_message = f"Redis 入队失败: {str(exc)[:300]}"
        db.commit()
        return repo.get_task(db, row.id)
    row.status = TASK_QUEUED
    row.error_message = None
    db.commit()
    return repo.get_task(db, row.id)


def list_tasks(db: Session, user: User) -> list[RpaTask]:
    return repo.list_tasks(db, None if user.role.code == "admin" else user.id)


def get_task(db: Session, user: User, *, task_id: int) -> RpaTask:
    return _get_task_for_user(db, user, task_id)


def list_mock_skus(db: Session, user: User) -> list[MockPlatformSku]:
    _require_admin(user)
    return repo.list_mock_skus(db)


def save_mock_sku(db: Session, user: User, *, row_id: int | None = None, **fields) -> MockPlatformSku:
    _require_admin(user)
    if fields["reserved_quantity"] > fields["available_quantity"]:
        raise AppException(ErrorCode.PARAM_ERROR, "锁定库存不能大于可用库存")
    starts_at = fields.get("promotion_starts_at")
    ends_at = fields.get("promotion_ends_at")
    if fields.get("live_price") is not None and (starts_at is None or ends_at is None):
        raise AppException(ErrorCode.PARAM_ERROR, "填写直播价时必须设置活动开始和结束时间")
    if starts_at is not None and ends_at is not None and starts_at >= ends_at:
        raise AppException(ErrorCode.PARAM_ERROR, "活动开始时间必须早于结束时间")
    code = fields["sku_code"].strip().upper()
    existing = repo.get_mock_sku_by_code(db, code)
    if row_id is None:
        if existing is not None:
            raise AppException(ErrorCode.PARAM_ERROR, "模拟平台 SKU 编码已存在")
        row = repo.create_mock_sku(db, **{**fields, "sku_code": code, "title": fields["title"].strip()})
    else:
        row = repo.get_mock_sku(db, row_id)
        if row is None:
            raise AppException(ErrorCode.NOT_FOUND, "模拟平台商品不存在")
        if existing is not None and existing.id != row.id:
            raise AppException(ErrorCode.PARAM_ERROR, "模拟平台 SKU 编码已存在")
        for key, value in fields.items():
            setattr(row, key, value)
        row.sku_code = code
        row.title = fields["title"].strip()
    db.commit()
    db.refresh(row)
    return row


def initialize_mock_skus(db: Session, user: User) -> list[MockPlatformSku]:
    _require_admin(user)
    for product in commerce_repo.list_products(db):
        for sku in product.skus:
            if repo.get_mock_sku_by_code(db, sku.sku_code) is not None:
                continue
            repo.create_mock_sku(
                db,
                sku_code=sku.sku_code,
                title=f"{product.name} {sku.specification}",
                platform_status=sku.status,
                list_price=sku.list_price,
                live_price=None,
                available_quantity=0,
                reserved_quantity=0,
                promotion_starts_at=None,
                promotion_ends_at=None,
                stackable=False,
            )
    db.commit()
    return repo.list_mock_skus(db)


# ---------- 高风险操作审批（阶段四） ----------

APPROVAL_TASK_TYPES = (TASK_PRICE_FIX, TASK_DELIST)


def _normalize_expected(task_type: str, expected_value: dict | None) -> dict:
    """把审批单的"预期值"规整成唯一形态 —— 执行与验证都按它比对。

    为什么在入口就规整（而不是执行时再转）：
    审批时看到的数字必须就是执行时用的数字。`99` / `99.0` / `"99.00"` 若原样存进 JSON，
    执行端和验证端各转一次、就有一处可能转错；这里统一成 `{"live_price": "99.00"}`。
    """
    expected = expected_value or {}
    if task_type == TASK_PRICE_FIX:
        raw = expected.get("live_price")
        try:
            price = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise AppException(ErrorCode.PARAM_ERROR, "改价申请必须提供合法的 live_price")
        if price <= 0:
            raise AppException(ErrorCode.PARAM_ERROR, "直播价必须大于 0")
        return {"live_price": f"{price:.2f}"}
    if expected.get("platform_status") != "inactive":
        raise AppException(ErrorCode.PARAM_ERROR, "下架申请的预期状态必须是 inactive")
    return {"platform_status": "inactive"}


def create_approval(
    db: Session,
    user: User,
    *,
    session_id: int,
    task_type: str,
    sku_id: int,
    expected_value: dict | None,
    reason: str | None = None,
    source_inspection_result_id: int | None = None,
) -> RpaApproval:
    """从一场直播里发起高风险操作申请（改价 / 下架）。

    三条硬约束：
      · 只有场次负责人或管理员能发起（与巡检同一口径）；
      · SKU 必须**在本场选品里** —— 不允许修一场没选的商品；
      · 同对象同类型只允许一张未结单（pending/approved），避免重复审批造成多次写入。
    """
    session = live_commerce_service.get_session(db, user, session_id)
    if task_type not in APPROVAL_TASK_TYPES:
        raise AppException(ErrorCode.PARAM_ERROR, "不支持的审批类型")
    if not any(item.sku_id == sku_id for item in session.items):
        raise AppException(ErrorCode.PARAM_ERROR, "该 SKU 不在本场选品中")
    expected = _normalize_expected(task_type, expected_value)
    source_snapshot = None
    if source_inspection_result_id is not None:
        source = commerce_repo.get_inspection_result(db, source_inspection_result_id)
        batches = live_commerce_service.list_inspections(
            db, user, session_id=session_id
        )
        latest = batches[0] if batches else None
        if (
            source is None
            or source.batch.session_id != session_id
            or latest is None
            or source.batch_id != latest.id
            or source.status != "failed"
        ):
            raise AppException(
                ErrorCode.PARAM_ERROR, "审批草稿来源已失效，请重新运行 Agent 分析"
            )
        if source.sku_id != sku_id:
            raise AppException(ErrorCode.PARAM_ERROR, "审批目标与巡检风险的 SKU 不一致")
        if task_type != TASK_PRICE_FIX or source.rule_code != "PRICE_MATCH":
            raise AppException(ErrorCode.PARAM_ERROR, "该巡检风险不支持生成此类审批")
        planned = (source.evidence or {}).get("planned_unit_price")
        if planned is None or expected != _normalize_expected(
            TASK_PRICE_FIX, {"live_price": planned}
        ):
            raise AppException(ErrorCode.PARAM_ERROR, "目标价格与最新巡检计划价不一致")
        source_snapshot = {
            "inspection_id": source.batch_id,
            "risk_id": source.id,
            "rule_code": source.rule_code,
            "message": source.message,
            "suggestion": source.suggestion,
            "evidence": source.evidence or {},
        }

    if repo.find_open_approval(
        db, session_id=session_id, sku_id=sku_id, task_type=task_type
    ) is not None:
        raise AppException(
            ErrorCode.PARAM_ERROR, "该 SKU 已有待处理或已批准的同类申请，请先处理完"
        )

    row = repo.create_approval(
        db,
        session_id=session_id,
        task_type=task_type,
        sku_id=sku_id,
        expected_value=expected,
        idempotency_key=uuid4().hex,
        status=APPROVAL_PENDING,
        requested_by=user.id,
        reason=(reason or "").strip() or None,
        source_inspection_result_id=source_inspection_result_id,
        source_snapshot=source_snapshot,
    )
    db.commit()
    db.refresh(row)
    logger.info("审批申请创建 id=%s type=%s sku=%s by=%s", row.id, task_type, sku_id, user.username)
    return row


def list_approvals(db: Session, user: User) -> list[RpaApproval]:
    """管理员看全部；其他人看自己发起的，以及需要自己负责场次审批的。"""
    return repo.list_approvals(db, None if user.role.code == "admin" else user.id)


def get_operational_metrics(db: Session, user: User, *, days: int = 30) -> dict:
    """汇总当前用户可见的 RPA、审批和最新巡检状态。"""
    now = datetime.now()
    since = now - timedelta(days=days)
    owner_id = None if user.role.code == "admin" else user.id
    tasks = repo.list_tasks_since(db, since=since, owner_id=owner_id)
    approvals = repo.list_approvals_since(db, since=since, owner_id=owner_id)
    sessions = live_commerce_service.list_sessions(db, user)

    terminal_statuses = {TASK_SUCCEEDED, TASK_FAILED, TASK_QUEUE_FAILED}
    terminal = [task for task in tasks if task.status in terminal_statuses]
    completed_durations = [
        (task.completed_at - task.started_at).total_seconds()
        for task in terminal
        if task.started_at is not None and task.completed_at is not None
        and task.completed_at >= task.started_at
    ]
    succeeded = sum(task.status == TASK_SUCCEEDED for task in tasks)
    failed = sum(task.status in {TASK_FAILED, TASK_QUEUE_FAILED} for task in tasks)

    latest_by_session = {session.id: None for session in sessions}
    for inspection in commerce_repo.list_latest_inspection_batches(
        db, list(latest_by_session)
    ):
        latest_by_session[inspection.session_id] = inspection

    alerts = []
    failure_groups = {}
    for task in tasks:  # tasks 已按 id 倒序：每组保留最新任务作为处理入口
        if task.status in {TASK_FAILED, TASK_QUEUE_FAILED}:
            normalized_error = " ".join((task.error_message or "任务失败").split())[:200]
            key = (task.session_id, task.task_type, normalized_error)
            if key in failure_groups:
                failure_groups[key]["occurrence_count"] += 1
                continue
            failure_groups[key] = {
                "alert_type": "task_failed",
                "severity": "blocker",
                "title": f"RPA 任务 #{task.id} 执行失败",
                "detail": normalized_error,
                "session_id": task.session_id,
                "task_id": task.id,
                "approval_id": None,
                "occurred_at": task.completed_at or task.created_at,
                "occurrence_count": 1,
            }
    for alert in failure_groups.values():
        if alert["occurrence_count"] > 1:
            alert["detail"] += f"（相同异常已合并 {alert['occurrence_count']} 次）"
        alerts.append(alert)
    for approval in approvals:
        if approval.status == APPROVAL_PENDING:
            alerts.append({
                "alert_type": "approval_pending",
                "severity": "warning",
                "title": f"审批 #{approval.id} 等待处理",
                "detail": f"{approval.sku.sku_code} 的{('改价' if approval.task_type == TASK_PRICE_FIX else '下架')}申请尚未审批",
                "session_id": approval.session_id,
                "task_id": None,
                "approval_id": approval.id,
                "occurred_at": approval.created_at,
                "occurrence_count": 1,
            })
    for session in sessions:
        latest = latest_by_session[session.id]
        if latest is not None and latest.conclusion == "blocked":
            alerts.append({
                "alert_type": "session_blocked",
                "severity": "blocker",
                "title": f"直播场次 #{session.id} 仍被阻断",
                "detail": f"{session.title}：{latest.blocker_count} 个阻断项、{latest.warning_count} 个警告项",
                "session_id": session.id,
                "task_id": None,
                "approval_id": None,
                "occurred_at": latest.completed_at or latest.created_at,
                "occurrence_count": 1,
            })
    alerts.sort(key=lambda row: row["occurred_at"], reverse=True)

    task_types = {}
    for task in tasks:
        task_types[task.task_type] = task_types.get(task.task_type, 0) + 1
    ready = sum(
        latest is not None and latest.conclusion == "ready"
        for latest in latest_by_session.values()
    )
    blocked = sum(
        latest is not None and latest.conclusion == "blocked"
        for latest in latest_by_session.values()
    )
    return {
        "window_days": days,
        "generated_at": now,
        "tasks": {
            "total": len(tasks),
            "queued": sum(task.status == TASK_QUEUED for task in tasks),
            "running": sum(task.status == TASK_RUNNING for task in tasks),
            "succeeded": succeeded,
            "failed": failed,
            "terminal": len(terminal),
            "success_rate": round(succeeded / len(terminal) * 100, 2) if terminal else None,
            "avg_duration_seconds": (
                round(sum(completed_durations) / len(completed_durations), 2)
                if completed_durations else None
            ),
            "retry_task_count": sum(task.attempt_count > 1 for task in tasks),
        },
        "approvals": {
            "total": len(approvals),
            "pending": sum(row.status == APPROVAL_PENDING for row in approvals),
            "approved": sum(row.status == APPROVAL_APPROVED for row in approvals),
            "rejected": sum(row.status == APPROVAL_REJECTED for row in approvals),
            "executed": sum(row.status == APPROVAL_EXECUTED for row in approvals),
            "failed": sum(row.status == APPROVAL_FAILED for row in approvals),
        },
        "sessions": {
            "total": len(sessions),
            "ready": ready,
            "blocked": blocked,
            "without_inspection": len(sessions) - ready - blocked,
        },
        "task_types": task_types,
        "alerts": alerts[:20],
    }


def decide_approval(
    db: Session, user: User, *, approval_id: int, approve: bool, note: str | None = None
) -> RpaApproval:
    """审批（通过 / 驳回）。

    **通过 = 生成一张唯一的执行任务**：任务幂等键直接复用审批单的 key，
    数据库唯一约束兜住"重复点通过"与 worker 重试 —— 同一张审批单最多改一次价。
    申请人不能批自己的单（四眼原则）；审批人必须是场次负责人或管理员。
    """
    row = repo.get_approval(db, approval_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "审批单不存在")
    live_commerce_service.get_session(db, user, row.session_id)  # 权限：owner 或 admin
    if row.status != APPROVAL_PENDING:
        raise AppException(ErrorCode.PARAM_ERROR, "该申请已处理，不能重复审批")
    if row.requested_by == user.id:
        raise AppException(ErrorCode.FORBIDDEN, "申请人不能审批自己的申请")

    now = datetime.now()
    row.approved_by = user.id
    row.approved_at = now
    row.decision_note = (note or "").strip() or None

    if not approve:
        row.status = APPROVAL_REJECTED
        db.commit()
        logger.info("审批驳回 id=%s by=%s", row.id, user.username)
        return repo.get_approval(db, row.id)

    row.status = APPROVAL_APPROVED
    sku = commerce_repo.get_sku(db, row.sku_id)
    if sku is None:
        raise AppException(ErrorCode.NOT_FOUND, "目标 SKU 已不存在")
    task = repo.create_task(
        db,
        session_id=row.session_id,
        task_type=row.task_type,
        status=TASK_QUEUED,
        # 幂等键复用审批单：唯一约束保证"一张单最多执行一次"
        idempotency_key=row.idempotency_key,
        requested_by=user.id,
        payload={
            "approval_id": row.id,
            "session_id": row.session_id,
            "sku_code": sku.sku_code,
            "expected": row.expected_value,
        },
    )
    row.task_id = task.id
    db.commit()
    if _is_demo_sandbox_user(user):
        _complete_demo_task(db, task, approval=row)
        logger.info("演示沙箱审批已模拟执行 approval=%s task=%s", row.id, task.id)
        return repo.get_approval(db, row.id)
    try:
        _push_task(task.id)
    except Exception as exc:  # Redis 故障：保留可重新入队状态，审批本身仍算通过
        task.status = TASK_QUEUE_FAILED
        task.error_message = f"Redis 入队失败: {str(exc)[:300]}"
        db.commit()
    logger.info(
        "审批通过 id=%s by=%s -> task=%s（幂等键复用审批单）", row.id, user.username, task.id
    )
    return repo.get_approval(db, row.id)
