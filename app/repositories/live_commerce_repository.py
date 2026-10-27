"""直播电商业务数据访问；事务边界由 service 管理。"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.live_commerce import (
    InspectionBatch,
    InspectionRuleConfig,
    InspectionResult,
    LiveSession,
    LiveSessionItem,
    InventorySnapshot,
    PreparationPackage,
    Product,
    ProductSku,
    Promotion,
    PromotionSku,
)
from app.models.document import Document, STATUS_COMPLETED


def list_products(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).order_by(Product.id)))


def get_product(db: Session, product_id: int) -> Product | None:
    return db.get(Product, product_id)


def get_sku(db: Session, sku_id: int) -> ProductSku | None:
    return db.get(ProductSku, sku_id)


def get_sku_by_code(db: Session, sku_code: str) -> ProductSku | None:
    return db.scalar(select(ProductSku).where(ProductSku.sku_code == sku_code))


def create_product(db: Session, **fields) -> Product:
    row = Product(**fields)
    db.add(row)
    db.flush()
    return row


def create_sku(db: Session, **fields) -> ProductSku:
    row = ProductSku(**fields)
    db.add(row)
    db.flush()
    return row


def list_sessions(db: Session, owner_id: int | None) -> list[LiveSession]:
    stmt = select(LiveSession)
    if owner_id is not None:
        stmt = stmt.where(LiveSession.owner_id == owner_id)
    return list(db.scalars(stmt.order_by(LiveSession.starts_at.desc(), LiveSession.id.desc())))


def get_session(db: Session, session_id: int) -> LiveSession | None:
    return db.get(LiveSession, session_id)


def create_session(db: Session, **fields) -> LiveSession:
    row = LiveSession(**fields)
    db.add(row)
    db.flush()
    return row


def get_session_item(db: Session, session_id: int, sku_id: int) -> LiveSessionItem | None:
    return db.scalar(
        select(LiveSessionItem).where(
            LiveSessionItem.session_id == session_id,
            LiveSessionItem.sku_id == sku_id,
        )
    )


def create_session_item(db: Session, **fields) -> LiveSessionItem:
    row = LiveSessionItem(**fields)
    db.add(row)
    db.flush()
    return row


def next_package_version(db: Session, session_id: int) -> int:
    current = db.scalar(
        select(func.max(PreparationPackage.version)).where(
            PreparationPackage.session_id == session_id
        )
    )
    return (current or 0) + 1


def create_package(db: Session, **fields) -> PreparationPackage:
    row = PreparationPackage(**fields)
    db.add(row)
    db.flush()
    return row


def create_inventory_snapshot(db: Session, **fields) -> InventorySnapshot:
    row = InventorySnapshot(**fields)
    db.add(row)
    db.flush()
    return row


def list_latest_inventory(
    db: Session, sku_id: int | None = None
) -> list[InventorySnapshot]:
    ranked = (
        select(
            InventorySnapshot.id,
            func.row_number().over(
                partition_by=InventorySnapshot.sku_id,
                order_by=(InventorySnapshot.captured_at.desc(), InventorySnapshot.id.desc()),
            ).label("rank"),
        )
        .subquery()
    )
    stmt = select(InventorySnapshot).join(ranked, InventorySnapshot.id == ranked.c.id).where(
        ranked.c.rank == 1
    )
    if sku_id is not None:
        stmt = stmt.where(InventorySnapshot.sku_id == sku_id)
    return list(db.scalars(stmt.order_by(InventorySnapshot.sku_id)))


def create_promotion(db: Session, *, sku_ids: list[int], **fields) -> Promotion:
    row = Promotion(**fields)
    db.add(row)
    db.flush()
    for sku_id in sku_ids:
        db.add(PromotionSku(promotion_id=row.id, sku_id=sku_id))
    db.flush()
    return row


def list_promotions(db: Session, sku_id: int | None = None) -> list[Promotion]:
    stmt = select(Promotion).options(selectinload(Promotion.sku_links))
    if sku_id is not None:
        stmt = stmt.join(PromotionSku).where(PromotionSku.sku_id == sku_id)
    return list(db.scalars(stmt.order_by(Promotion.id.desc())).unique())


def list_active_promotions(db: Session, sku_id: int, at_time: datetime) -> list[Promotion]:
    stmt = (
        select(Promotion)
        .join(PromotionSku)
        .options(selectinload(Promotion.sku_links))
        .where(
            PromotionSku.sku_id == sku_id,
            Promotion.status == "active",
            Promotion.starts_at <= at_time,
            Promotion.ends_at >= at_time,
        )
        .order_by(Promotion.id)
    )
    return list(db.scalars(stmt).unique())


def get_latest_inventory(db: Session, sku_id: int) -> InventorySnapshot | None:
    return db.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.sku_id == sku_id)
        .order_by(InventorySnapshot.captured_at.desc(), InventorySnapshot.id.desc())
        .limit(1)
    )


def has_valid_document(
    db: Session,
    *,
    product_id: int,
    sku_id: int,
    document_type: str,
    effective_at: datetime,
) -> tuple[bool, list[int]]:
    stmt = select(Document.id).where(
        Document.product_id == product_id,
        or_(Document.sku_id.is_(None), Document.sku_id == sku_id),
        Document.document_type == document_type,
        Document.status == STATUS_COMPLETED,
        or_(Document.effective_from.is_(None), Document.effective_from <= effective_at),
        or_(Document.effective_to.is_(None), Document.effective_to >= effective_at),
    )
    ids = list(db.scalars(stmt.order_by(Document.id.desc())))
    return bool(ids), ids


def create_inspection_batch(db: Session, **fields) -> InspectionBatch:
    row = InspectionBatch(**fields)
    db.add(row)
    db.flush()
    return row


def create_inspection_result(db: Session, **fields) -> InspectionResult:
    row = InspectionResult(**fields)
    db.add(row)
    db.flush()
    return row


def list_inspection_batches(db: Session, session_id: int) -> list[InspectionBatch]:
    stmt = (
        select(InspectionBatch)
        .options(selectinload(InspectionBatch.results))
        .where(InspectionBatch.session_id == session_id)
        .order_by(InspectionBatch.id.desc())
    )
    return list(db.scalars(stmt))


def list_latest_inspection_batches(
    db: Session, session_ids: list[int]
) -> list[InspectionBatch]:
    """一次查询取每个场次的最新巡检，避免监控页按场次 N+1 查询。"""
    if not session_ids:
        return []
    latest_ids = (
        select(func.max(InspectionBatch.id).label("id"))
        .where(InspectionBatch.session_id.in_(session_ids))
        .group_by(InspectionBatch.session_id)
        .subquery()
    )
    return list(
        db.scalars(
            select(InspectionBatch)
            .options(selectinload(InspectionBatch.results))
            .where(InspectionBatch.id.in_(select(latest_ids.c.id)))
        )
    )


def get_inspection_batch(db: Session, batch_id: int) -> InspectionBatch | None:
    return db.scalar(
        select(InspectionBatch)
        .options(selectinload(InspectionBatch.results))
        .where(InspectionBatch.id == batch_id)
    )


def get_inspection_result(db: Session, result_id: int) -> InspectionResult | None:
    return db.get(InspectionResult, result_id)


def list_inspection_rule_configs(db: Session) -> list[InspectionRuleConfig]:
    return list(db.scalars(select(InspectionRuleConfig).order_by(InspectionRuleConfig.id)))


def get_inspection_rule_config(db: Session, rule_code: str) -> InspectionRuleConfig | None:
    return db.scalar(
        select(InspectionRuleConfig).where(InspectionRuleConfig.rule_code == rule_code)
    )


def upsert_rpa_live_price(
    db: Session,
    *,
    sku_id: int,
    sku_code: str,
    live_price,
    starts_at: datetime | None,
    ends_at: datetime | None,
    stackable: bool,
    created_by: int,
) -> Promotion | None:
    """按固定外部名称幂等同步模拟平台直播价。"""
    name = f"RPA同步直播价:{sku_code}"
    row = db.scalar(select(Promotion).where(Promotion.name == name))
    if live_price is None or starts_at is None or ends_at is None:
        if row is not None:
            row.status = "inactive"
            db.flush()
        return row
    if row is None:
        return create_promotion(
            db,
            sku_ids=[sku_id],
            name=name,
            promotion_type="live_price",
            fixed_price=live_price,
            threshold_amount=None,
            discount_amount=None,
            stackable=stackable,
            status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=created_by,
        )
    row.fixed_price = live_price
    row.stackable = stackable
    row.status = "active"
    row.starts_at = starts_at
    row.ends_at = ends_at
    if not any(link.sku_id == sku_id for link in row.sku_links):
        db.add(PromotionSku(promotion_id=row.id, sku_id=sku_id))
    db.flush()
    return row

