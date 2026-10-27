"""RPA 自动化任务与模拟平台数据接口。"""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.config import BASE_DIR
from app.core.exceptions import AppException
from app.core.response import ApiResponse, ErrorCode, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.rpa import (
    MockPlatformSkuCreate,
    MockPlatformSkuOut,
    RpaApprovalCreate,
    RpaApprovalDecision,
    RpaApprovalOut,
    RpaTaskCreate,
    RpaTaskOut,
    RpaMetricsOut,
    MonitoringPlanOut,
    MonitoringPlanUpsert,
)
from app.services import rpa_service

router = APIRouter(prefix="/rpa", tags=["RPA 自动化"])


@router.get("/metrics", response_model=ApiResponse[RpaMetricsOut])
def get_metrics(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if days < 1 or days > 365:
        raise AppException(ErrorCode.PARAM_ERROR, "统计窗口必须在 1 到 365 天之间")
    return success(
        rpa_service.get_operational_metrics(db, current_user, days=days),
        "运营监控指标已更新",
    )


@router.get("/monitoring-plans", response_model=ApiResponse[list[MonitoringPlanOut]])
def list_monitoring_plans(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = rpa_service.list_monitoring_plans(db, current_user)
    return success([MonitoringPlanOut.model_validate(row) for row in rows])


@router.put(
    "/monitoring-plans/{session_id}",
    response_model=ApiResponse[MonitoringPlanOut],
)
def upsert_monitoring_plan(
    session_id: int,
    payload: MonitoringPlanUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = rpa_service.upsert_monitoring_plan(
        db, current_user, session_id=session_id, **payload.model_dump()
    )
    return success(MonitoringPlanOut.model_validate(row), "定时监控计划已保存")


@router.get("/tasks", response_model=ApiResponse[list[RpaTaskOut]])
def list_tasks(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = rpa_service.list_tasks(db, current_user)
    return success([RpaTaskOut.model_validate(row) for row in rows])


@router.post("/tasks", response_model=ApiResponse[RpaTaskOut])
def create_task(
    payload: RpaTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = rpa_service.create_collection_task(
        db, current_user, session_id=payload.session_id
    )
    message = "自动化任务已进入队列" if row.status == "queued" else "任务已保存，但 Redis 入队失败"
    return success(RpaTaskOut.model_validate(row), message)


@router.get("/tasks/{task_id}", response_model=ApiResponse[RpaTaskOut])
def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return success(RpaTaskOut.model_validate(rpa_service.get_task(db, current_user, task_id=task_id)))


@router.post("/tasks/{task_id}/requeue", response_model=ApiResponse[RpaTaskOut])
def requeue_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = rpa_service.requeue_task(db, current_user, task_id=task_id)
    return success(RpaTaskOut.model_validate(row), "任务已重新入队")


@router.get("/tasks/{task_id}/steps/{step_id}/screenshot", summary="下载 RPA 步骤截图")
def download_step_screenshot(
    task_id: int,
    step_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按步骤取截图（**有归属校验**）。

    为什么不做静态目录挂载：截图是执行证据，目录里还有其他任务的证据；
    静态挂载等于把"谁的证据"这个判断交出去（任何登录用户都能按文件名猜）。
    这里先走 `get_task` 做归属校验（admin 或任务发起人），再按库里记的路径取文件，
    路径来自数据库而不是请求参数 —— 顺带杜绝了目录穿越。
    """
    task = rpa_service.get_task(db, current_user, task_id=task_id)
    step = next((s for s in task.steps if s.id == step_id), None)
    if step is None or not step.screenshot_path:
        raise AppException(ErrorCode.NOT_FOUND, "该步骤没有截图")
    path = Path(BASE_DIR) / step.screenshot_path
    if not path.is_file():
        raise AppException(ErrorCode.NOT_FOUND, "截图文件已不存在（可能被清理）")
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/mock-platform/skus", response_model=ApiResponse[list[MockPlatformSkuOut]])
def list_mock_skus(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    rows = rpa_service.list_mock_skus(db, admin)
    return success([MockPlatformSkuOut.model_validate(row) for row in rows])


@router.post("/mock-platform/skus", response_model=ApiResponse[MockPlatformSkuOut])
def create_mock_sku(
    payload: MockPlatformSkuCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = rpa_service.save_mock_sku(db, admin, **payload.model_dump())
    return success(MockPlatformSkuOut.model_validate(row), "模拟平台商品已创建")


@router.put("/mock-platform/skus/{row_id}", response_model=ApiResponse[MockPlatformSkuOut])
def update_mock_sku(
    row_id: int,
    payload: MockPlatformSkuCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = rpa_service.save_mock_sku(db, admin, row_id=row_id, **payload.model_dump())
    return success(MockPlatformSkuOut.model_validate(row), "模拟平台商品已更新")


@router.post("/mock-platform/initialize", response_model=ApiResponse[list[MockPlatformSkuOut]])
def initialize_mock_skus(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    rows = rpa_service.initialize_mock_skus(db, admin)
    return success([MockPlatformSkuOut.model_validate(row) for row in rows], "模拟平台数据已初始化")


# ---------- 高风险操作审批 ----------


def _approval_out(row) -> RpaApprovalOut:
    out = RpaApprovalOut.model_validate(row)
    out.sku_code = row.sku.sku_code if row.sku else None
    return out


@router.get("/approvals", response_model=ApiResponse[list[RpaApprovalOut]])
def list_approvals(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = rpa_service.list_approvals(db, current_user)
    return success([_approval_out(row) for row in rows])


@router.post("/approvals", response_model=ApiResponse[RpaApprovalOut])
def create_approval(
    payload: RpaApprovalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = rpa_service.create_approval(
        db,
        current_user,
        session_id=payload.session_id,
        task_type=payload.task_type,
        sku_id=payload.sku_id,
        expected_value=payload.expected_value,
        reason=payload.reason,
        source_inspection_result_id=payload.source_inspection_result_id,
    )
    return success(_approval_out(row), "审批申请已提交")


@router.post("/approvals/{approval_id}/decision", response_model=ApiResponse[RpaApprovalOut])
def decide_approval(
    approval_id: int,
    payload: RpaApprovalDecision,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = rpa_service.decide_approval(
        db, current_user, approval_id=approval_id,
        approve=payload.approve, note=payload.note,
    )
    return success(
        _approval_out(row),
        "已通过，执行任务已生成" if payload.approve else "已驳回",
    )
