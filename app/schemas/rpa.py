"""RPA 与模拟平台接口模型。"""

from datetime import datetime
from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RpaTaskCreate(BaseModel):
    session_id: int = Field(ge=1)


class RpaTaskStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_code: str
    step_name: str
    status: str
    message: str | None
    screenshot_path: str | None
    started_at: datetime
    completed_at: datetime | None


class RpaTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    task_type: str
    status: str
    idempotency_key: str
    requested_by: int
    payload: dict
    result: dict | None
    error_message: str | None
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    steps: list[RpaTaskStepOut] = Field(default_factory=list)


class MockPlatformSkuCreate(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=128)
    platform_status: str = Field(default="active", pattern=r"^(active|inactive)$")
    list_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    live_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    available_quantity: int = Field(default=0, ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    promotion_starts_at: datetime | None = None
    promotion_ends_at: datetime | None = None
    stackable: bool = False


class MockPlatformSkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    title: str
    platform_status: str
    list_price: Decimal
    live_price: Decimal | None
    available_quantity: int
    reserved_quantity: int
    promotion_starts_at: datetime | None
    promotion_ends_at: datetime | None
    stackable: bool
    updated_at: datetime


# ---------- 高风险操作审批 ----------


class RpaApprovalCreate(BaseModel):
    session_id: int = Field(ge=1)
    task_type: Literal["price_fix", "delist"]
    sku_id: int = Field(ge=1)
    expected_value: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=512)
    source_inspection_result_id: int | None = Field(default=None, ge=1)


class RpaApprovalDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=512)


class RpaApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    task_type: str
    sku_id: int
    # 目标 SKU 编码由 API 层从关系里填（from_attributes 不会扁平化 sku.sku_code）
    sku_code: str | None = None
    expected_value: dict
    status: str
    requested_by: int
    reason: str | None
    approved_by: int | None
    approved_at: datetime | None
    decision_note: str | None
    task_id: int | None
    source_inspection_result_id: int | None
    source_snapshot: dict | None
    created_at: datetime


class RpaMetricTaskSummary(BaseModel):
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int
    terminal: int
    success_rate: float | None
    avg_duration_seconds: float | None
    retry_task_count: int


class RpaMetricApprovalSummary(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int
    executed: int
    failed: int


class RpaMetricSessionSummary(BaseModel):
    total: int
    ready: int
    blocked: int
    without_inspection: int


class RpaMetricAlert(BaseModel):
    alert_type: Literal["task_failed", "approval_pending", "session_blocked"]
    severity: Literal["blocker", "warning"]
    title: str
    detail: str
    session_id: int
    task_id: int | None = None
    approval_id: int | None = None
    occurred_at: datetime
    occurrence_count: int = 1


class RpaMetricsOut(BaseModel):
    window_days: int
    generated_at: datetime
    tasks: RpaMetricTaskSummary
    approvals: RpaMetricApprovalSummary
    sessions: RpaMetricSessionSummary
    task_types: dict[str, int]
    alerts: list[RpaMetricAlert] = Field(default_factory=list)


class MonitoringPlanUpsert(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(default=30, ge=5, le=1440)


class MonitoringPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    enabled: bool
    interval_minutes: int
    next_run_at: datetime
    last_run_at: datetime | None
    last_task_id: int | None
    created_by: int
    created_at: datetime
    updated_at: datetime
