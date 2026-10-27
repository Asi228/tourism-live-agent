"""直播电商业务接口模型。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ProductStatus = Literal["active", "inactive"]
SessionStatus = Literal[
    "draft", "checking", "needs_confirmation", "ready", "published", "finished"
]


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    brand: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)


class SkuCreate(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    specification: str = Field(min_length=1, max_length=128)
    list_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="CNY", min_length=3, max_length=3)


class SkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    sku_code: str
    specification: str
    list_price: Decimal
    currency: str
    status: str
    created_at: datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    brand: str | None
    category: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    skus: list[SkuOut] = Field(default_factory=list)


class LiveSessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    starts_at: datetime


class SessionItemCreate(BaseModel):
    sku_id: int = Field(ge=1)
    planned_sales: int = Field(default=0, ge=0)
    planned_unit_price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    sort_order: int = Field(default=0, ge=0)


class SessionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    planned_sales: int
    planned_unit_price: Decimal | None
    sort_order: int
    sku: SkuOut


class PreparationPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    version: int
    status: str
    content: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class LiveSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    starts_at: datetime
    status: str
    owner_id: int
    created_at: datetime
    updated_at: datetime
    items: list[SessionItemOut] = Field(default_factory=list)
    packages: list[PreparationPackageOut] = Field(default_factory=list)


class InventorySnapshotCreate(BaseModel):
    sku_id: int = Field(ge=1)
    available_quantity: int = Field(ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    source: str = Field(default="manual", min_length=1, max_length=32)
    captured_at: datetime | None = None


class InventorySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    available_quantity: int
    reserved_quantity: int
    sellable_quantity: int
    source: str
    captured_at: datetime
    created_by: int
    sku: SkuOut


PromotionType = Literal["live_price", "threshold_discount", "coupon"]


class PromotionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    promotion_type: PromotionType
    sku_ids: list[int] = Field(min_length=1)
    fixed_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    threshold_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    discount_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    stackable: bool = False
    starts_at: datetime
    ends_at: datetime


class PromotionSkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku_id: int
    sku: SkuOut


class PromotionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    promotion_type: str
    fixed_price: Decimal | None
    threshold_amount: Decimal | None
    discount_amount: Decimal | None
    stackable: bool
    status: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    sku_links: list[PromotionSkuOut] = Field(default_factory=list)


class PricePreviewRequest(BaseModel):
    sku_id: int = Field(ge=1)
    quantity: int = Field(default=1, ge=1, le=100000)
    at_time: datetime | None = None


class PriceAdjustmentOut(BaseModel):
    promotion_id: int
    name: str
    promotion_type: str
    amount: Decimal


class PricePreviewOut(BaseModel):
    sku_id: int
    sku_code: str
    quantity: int
    unit_list_price: Decimal
    original_total: Decimal
    final_total: Decimal
    final_unit_price: Decimal
    saved_amount: Decimal
    calculated_at: datetime
    applied_promotions: list[PriceAdjustmentOut]
    ignored_promotion_ids: list[int]


class InspectionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_code: str
    rule_name: str
    severity: str
    status: str
    sku_id: int | None
    message: str
    suggestion: str | None
    evidence: dict
    created_at: datetime
    sku: SkuOut | None = None


class InspectionBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    status: str
    conclusion: str | None
    score: int
    blocker_count: int
    warning_count: int
    passed_count: int
    rule_version: str
    checked_by: int
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    results: list[InspectionResultOut] = Field(default_factory=list)


class InspectionRuleConfigUpdate(BaseModel):
    enabled: bool | None = None
    severity: Literal["blocker", "warning", "info"] | None = None
    parameters: dict | None = None


class InspectionRuleConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_code: str
    rule_name: str
    description: str
    enabled: bool
    severity: str
    parameters: dict
    version: int
    updated_by: int | None
    updated_at: datetime


# ---------- 单场直播复盘 ----------


class SessionRecapItemOut(BaseModel):
    sku_id: int
    sku_code: str
    product_name: str
    specification: str
    planned_sales: int
    planned_unit_price: Decimal | None
    planned_gmv: Decimal | None


class SessionRecapTaskOut(BaseModel):
    id: int
    task_type: str
    status: str
    attempt_count: int
    duration_seconds: float | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class SessionRecapApprovalOut(BaseModel):
    id: int
    task_type: str
    sku_code: str
    status: str
    reason: str | None
    decision_note: str | None
    decision_seconds: float | None
    task_id: int | None
    created_at: datetime
    approved_at: datetime | None


class SessionRecapInspectionOut(BaseModel):
    total_runs: int
    latest: InspectionBatchOut | None
    unresolved_risks: list[InspectionResultOut] = Field(default_factory=list)


class SessionRecapAutomationOut(BaseModel):
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int
    success_rate: float | None
    avg_duration_seconds: float | None
    retry_task_count: int
    tasks: list[SessionRecapTaskOut] = Field(default_factory=list)


class SessionRecapApprovalSummaryOut(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int
    executed: int
    failed: int
    avg_decision_seconds: float | None
    approvals: list[SessionRecapApprovalOut] = Field(default_factory=list)


class LiveSessionRecapOut(BaseModel):
    session_id: int
    title: str
    status: str
    starts_at: datetime
    owner_id: int
    generated_at: datetime
    headline: str
    readiness: Literal["ready", "blocked", "not_inspected"]
    sku_count: int
    planned_sales: int
    planned_gmv: Decimal
    items: list[SessionRecapItemOut] = Field(default_factory=list)
    inspection: SessionRecapInspectionOut
    automation: SessionRecapAutomationOut
    approvals: SessionRecapApprovalSummaryOut

