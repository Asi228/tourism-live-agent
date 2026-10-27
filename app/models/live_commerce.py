"""直播电商业务骨架。

知识库负责保存“商品怎么讲、规则怎么规定”，本模块保存会变化的结构化业务事实：
商品/SKU、直播场次、场次选品和准备包版本。两类数据分开后，库存、价格等实时事实
不会被旧文档覆盖，后续 Agent 也能通过明确的业务工具读取它们。
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


PRODUCT_ACTIVE = "active"
PRODUCT_INACTIVE = "inactive"
SKU_ACTIVE = "active"
SKU_INACTIVE = "inactive"

SESSION_DRAFT = "draft"
SESSION_CHECKING = "checking"
SESSION_NEEDS_CONFIRMATION = "needs_confirmation"
SESSION_READY = "ready"
SESSION_PUBLISHED = "published"
SESSION_FINISHED = "finished"
SESSION_STATUSES = (
    SESSION_DRAFT,
    SESSION_CHECKING,
    SESSION_NEEDS_CONFIRMATION,
    SESSION_READY,
    SESSION_PUBLISHED,
    SESSION_FINISHED,
)

PACKAGE_DRAFT = "draft"
PACKAGE_STALE = "stale"
PACKAGE_PUBLISHED = "published"
PACKAGE_STATUSES = (PACKAGE_DRAFT, PACKAGE_STALE, PACKAGE_PUBLISHED)

PROMOTION_LIVE_PRICE = "live_price"
PROMOTION_THRESHOLD = "threshold_discount"
PROMOTION_COUPON = "coupon"
PROMOTION_TYPES = (PROMOTION_LIVE_PRICE, PROMOTION_THRESHOLD, PROMOTION_COUPON)

INSPECTION_RUNNING = "running"
INSPECTION_COMPLETED = "completed"
INSPECTION_FAILED = "failed"
INSPECTION_READY = "ready"
INSPECTION_BLOCKED = "blocked"

RULE_PASSED = "passed"
RULE_FAILED = "failed"
SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


class Product(Base):
    """商品主档；具体可售规格放在 ``ProductSku``。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=PRODUCT_ACTIVE, server_default=PRODUCT_ACTIVE,
        index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    skus: Mapped[list["ProductSku"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductSku(Base):
    """可被选进直播场次的最小商品单位。"""

    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    specification: Mapped[str] = mapped_column(String(128), nullable=False)
    list_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="CNY", server_default="CNY", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=SKU_ACTIVE, server_default=SKU_ACTIVE,
        index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="skus", lazy="joined")


class LiveSession(Base):
    """一场直播的工作空间。普通用户只能操作自己负责的场次。"""

    __tablename__ = "live_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=SESSION_DRAFT, server_default=SESSION_DRAFT,
        index=True, nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner: Mapped["User"] = relationship(lazy="joined")
    items: Mapped[list["LiveSessionItem"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="LiveSessionItem.sort_order",
    )
    packages: Mapped[list["PreparationPackage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="PreparationPackage.version",
    )
    inspections: Mapped[list["InspectionBatch"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="InspectionBatch.id",
    )


class LiveSessionItem(Base):
    """场次选品；同一 SKU 在一个场次中只能出现一次。"""

    __tablename__ = "live_session_items"
    __table_args__ = (
        UniqueConstraint("session_id", "sku_id", name="uq_live_session_item_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("product_skus.id"), index=True, nullable=False
    )
    planned_sales: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    planned_unit_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="运营计划到手单价；用于价格巡检"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped[LiveSession] = relationship(back_populates="items")
    sku: Mapped[ProductSku] = relationship(lazy="joined")


class PreparationPackage(Base):
    """开播准备包版本；正文后续由规则核验与 Agent 共同生成。"""

    __tablename__ = "preparation_packages"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_preparation_package_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=PACKAGE_DRAFT, server_default=PACKAGE_DRAFT,
        index=True, nullable=False,
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped[LiveSession] = relationship(back_populates="packages")


class InventorySnapshot(Base):
    """SKU 库存快照；保留历史，核验结果才能说明当时依据。"""

    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("product_skus.id", ondelete="CASCADE"), index=True, nullable=False
    )
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual", nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    sku: Mapped[ProductSku] = relationship(lazy="joined")

    @property
    def sellable_quantity(self) -> int:
        return max(0, self.available_quantity - self.reserved_quantity)


class Promotion(Base):
    """促销规则主表；适用 SKU 通过 ``PromotionSku`` 关联。"""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    promotion_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    fixed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    threshold_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    stackable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", index=True, nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sku_links: Mapped[list["PromotionSku"]] = relationship(
        back_populates="promotion", cascade="all, delete-orphan"
    )


class PromotionSku(Base):
    __tablename__ = "promotion_skus"
    __table_args__ = (
        UniqueConstraint("promotion_id", "sku_id", name="uq_promotion_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("promotions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("product_skus.id", ondelete="CASCADE"), index=True, nullable=False
    )

    promotion: Mapped[Promotion] = relationship(back_populates="sku_links")
    sku: Mapped[ProductSku] = relationship(lazy="joined")


class InspectionBatch(Base):
    """一次不可变的直播前巡检批次，保留当时结论和规则版本。"""

    __tablename__ = "inspection_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=INSPECTION_RUNNING, server_default=INSPECTION_RUNNING,
        index=True, nullable=False,
    )
    conclusion: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    blocker_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    rule_version: Mapped[str] = mapped_column(
        String(32), default="2026.10-v1", server_default="2026.10-v1", nullable=False
    )
    checked_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped[LiveSession] = relationship(back_populates="inspections")
    results: Mapped[list["InspectionResult"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan",
        order_by="InspectionResult.id",
    )


class InspectionResult(Base):
    """单条规则的确定性结果；evidence 保存形成结论时使用的数据。"""

    __tablename__ = "inspection_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_batches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_skus.id", ondelete="SET NULL"), index=True, nullable=True
    )
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    suggestion: Mapped[str | None] = mapped_column(String(512), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    batch: Mapped[InspectionBatch] = relationship(back_populates="results")
    sku: Mapped[ProductSku | None] = relationship(lazy="joined")


class InspectionRuleConfig(Base):
    """企业可配置的直播巡检规则。

    规则算法仍由代码实现，企业管理员只配置是否启用、风险等级和少量安全参数，
    避免把可执行表达式存进数据库造成安全与审计问题。
    """

    __tablename__ = "inspection_rule_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True, nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updater: Mapped["User | None"] = relationship(lazy="joined")

