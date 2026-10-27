"""直播电商商品、场次与准备包基础接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.live_commerce import (
    InventorySnapshotCreate,
    InventorySnapshotOut,
    InspectionBatchOut,
    InspectionRuleConfigOut,
    InspectionRuleConfigUpdate,
    LiveSessionCreate,
    LiveSessionOut,
    LiveSessionRecapOut,
    PreparationPackageOut,
    ProductCreate,
    ProductOut,
    PromotionCreate,
    PromotionOut,
    PricePreviewOut,
    PricePreviewRequest,
    SessionItemCreate,
    SessionItemOut,
    SkuCreate,
    SkuOut,
)
from app.services import live_commerce_service

router = APIRouter(prefix="/commerce", tags=["直播电商"])


@router.get("/inspection-rules", response_model=ApiResponse[list[InspectionRuleConfigOut]])
def list_inspection_rules(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = live_commerce_service.list_inspection_rules(db)
    return success([InspectionRuleConfigOut.model_validate(row) for row in rows])


@router.patch(
    "/inspection-rules/{rule_code}",
    response_model=ApiResponse[InspectionRuleConfigOut],
)
def update_inspection_rule(
    rule_code: str,
    payload: InspectionRuleConfigUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.update_inspection_rule(
        db, admin, rule_code=rule_code, **payload.model_dump()
    )
    return success(InspectionRuleConfigOut.model_validate(row), "巡检规则已更新")


@router.get("/products", response_model=ApiResponse[list[ProductOut]])
def list_products(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return success([ProductOut.model_validate(x) for x in live_commerce_service.list_products(db)])


@router.post("/products", response_model=ApiResponse[ProductOut])
def create_product(
    payload: ProductCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_product(
        db, admin, name=payload.name, brand=payload.brand, category=payload.category
    )
    return success(ProductOut.model_validate(row), "商品已创建")


@router.post("/products/{product_id}/skus", response_model=ApiResponse[SkuOut])
def create_sku(
    product_id: int,
    payload: SkuCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_sku(
        db, admin, product_id=product_id, **payload.model_dump()
    )
    return success(SkuOut.model_validate(row), "SKU 已创建")


@router.get("/sessions", response_model=ApiResponse[list[LiveSessionOut]])
def list_sessions(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = live_commerce_service.list_sessions(db, current_user)
    return success([LiveSessionOut.model_validate(x) for x in rows])


@router.post("/sessions", response_model=ApiResponse[LiveSessionOut])
def create_session(
    payload: LiveSessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_session(
        db, current_user, title=payload.title, starts_at=payload.starts_at
    )
    return success(LiveSessionOut.model_validate(row), "直播场次已创建")


@router.get("/sessions/{session_id}", response_model=ApiResponse[LiveSessionOut])
def get_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return success(
        LiveSessionOut.model_validate(
            live_commerce_service.get_session(db, current_user, session_id)
        )
    )


@router.get(
    "/sessions/{session_id}/recap",
    response_model=ApiResponse[LiveSessionRecapOut],
)
def get_session_recap(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return success(
        live_commerce_service.get_session_recap(
            db, current_user, session_id=session_id
        ),
        "直播复盘已生成",
    )


@router.post(
    "/sessions/{session_id}/items", response_model=ApiResponse[SessionItemOut]
)
def add_session_item(
    session_id: int,
    payload: SessionItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.add_session_item(
        db, current_user, session_id=session_id, **payload.model_dump()
    )
    return success(SessionItemOut.model_validate(row), "选品已添加")


@router.post(
    "/sessions/{session_id}/packages", response_model=ApiResponse[PreparationPackageOut]
)
def create_package_draft(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_package_draft(
        db, current_user, session_id=session_id
    )
    return success(PreparationPackageOut.model_validate(row), "准备包草稿已创建")


@router.get("/inventory", response_model=ApiResponse[list[InventorySnapshotOut]])
def list_inventory(
    sku_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = live_commerce_service.list_latest_inventory(db, sku_id=sku_id)
    return success([InventorySnapshotOut.model_validate(row) for row in rows])


@router.post("/inventory", response_model=ApiResponse[InventorySnapshotOut])
def create_inventory_snapshot(
    payload: InventorySnapshotCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_inventory_snapshot(
        db, admin, **payload.model_dump()
    )
    return success(InventorySnapshotOut.model_validate(row), "库存快照已记录")


@router.get("/promotions", response_model=ApiResponse[list[PromotionOut]])
def list_promotions(
    sku_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = live_commerce_service.list_promotions(db, sku_id=sku_id)
    return success([PromotionOut.model_validate(row) for row in rows])


@router.post("/promotions", response_model=ApiResponse[PromotionOut])
def create_promotion(
    payload: PromotionCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.create_promotion(db, admin, **payload.model_dump())
    return success(PromotionOut.model_validate(row), "促销活动已创建")


@router.post("/price-preview", response_model=ApiResponse[PricePreviewOut])
def preview_price(
    payload: PricePreviewRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = live_commerce_service.calculate_price(db, **payload.model_dump())
    return success(PricePreviewOut.model_validate(result), "价格计算完成")


@router.post(
    "/sessions/{session_id}/inspections",
    response_model=ApiResponse[InspectionBatchOut],
)
def run_inspection(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.run_inspection(
        db, current_user, session_id=session_id
    )
    return success(InspectionBatchOut.model_validate(row), "直播巡检完成")


@router.get(
    "/sessions/{session_id}/inspections",
    response_model=ApiResponse[list[InspectionBatchOut]],
)
def list_inspections(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = live_commerce_service.list_inspections(
        db, current_user, session_id=session_id
    )
    return success([InspectionBatchOut.model_validate(row) for row in rows])


@router.get(
    "/inspections/{batch_id}",
    response_model=ApiResponse[InspectionBatchOut],
)
def get_inspection(
    batch_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = live_commerce_service.get_inspection(
        db, current_user, batch_id=batch_id
    )
    return success(InspectionBatchOut.model_validate(row))

