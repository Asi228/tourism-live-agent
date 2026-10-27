"""直播电商业务骨架的权限与事务编排。"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.live_commerce import (
    INSPECTION_BLOCKED,
    INSPECTION_COMPLETED,
    INSPECTION_READY,
    INSPECTION_RUNNING,
    PROMOTION_COUPON,
    PROMOTION_LIVE_PRICE,
    PROMOTION_THRESHOLD,
    PROMOTION_TYPES,
    RULE_FAILED,
    RULE_PASSED,
    SESSION_CHECKING,
    SESSION_NEEDS_CONFIRMATION,
    SESSION_READY,
    SEVERITY_BLOCKER,
    SEVERITY_WARNING,
    InspectionBatch,
    InspectionRuleConfig,
    InventorySnapshot,
    LiveSession,
    PreparationPackage,
    Product,
    ProductSku,
    Promotion,
)
from app.models.document import DOC_TYPE_CLAIM_EVIDENCE, DOC_TYPE_PRODUCT_MANUAL
from app.models.user import User
from app.models.rpa import (
    APPROVAL_APPROVED,
    APPROVAL_EXECUTED,
    APPROVAL_FAILED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TASK_FAILED,
    TASK_QUEUE_FAILED,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
)
from app.repositories import live_commerce_repository as repo
from app.repositories import rpa_repository as rpa_repo


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _require_admin(user: User) -> None:
    if user.role.code != "admin":
        raise AppException(ErrorCode.FORBIDDEN, "需要管理员权限")


def _get_owned_session(db: Session, user: User, session_id: int) -> LiveSession:
    row = repo.get_session(db, session_id)
    if row is None or (user.role.code != "admin" and row.owner_id != user.id):
        raise AppException(ErrorCode.NOT_FOUND, "直播场次不存在或没有访问权限")
    return row


def list_products(db: Session) -> list[Product]:
    return repo.list_products(db)


def create_product(
    db: Session, user: User, *, name: str, brand: str | None, category: str | None
) -> Product:
    _require_admin(user)
    row = repo.create_product(
        db, name=name.strip(), brand=_clean(brand), category=_clean(category)
    )
    db.commit()
    db.refresh(row)
    return row


def create_sku(
    db: Session,
    user: User,
    *,
    product_id: int,
    sku_code: str,
    specification: str,
    list_price: Decimal,
    currency: str,
) -> ProductSku:
    _require_admin(user)
    if repo.get_product(db, product_id) is None:
        raise AppException(ErrorCode.NOT_FOUND, "商品不存在")
    code = sku_code.strip().upper()
    if repo.get_sku_by_code(db, code) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "SKU 编码已存在")
    row = repo.create_sku(
        db,
        product_id=product_id,
        sku_code=code,
        specification=specification.strip(),
        list_price=list_price,
        currency=currency.upper(),
    )
    db.commit()
    db.refresh(row)
    return row


def list_sessions(db: Session, user: User) -> list[LiveSession]:
    return repo.list_sessions(db, None if user.role.code == "admin" else user.id)


def get_session(db: Session, user: User, session_id: int) -> LiveSession:
    return _get_owned_session(db, user, session_id)


def create_session(db: Session, user: User, *, title: str, starts_at) -> LiveSession:
    row = repo.create_session(
        db, title=title.strip(), starts_at=starts_at, owner_id=user.id
    )
    db.commit()
    db.refresh(row)
    return row


def add_session_item(
    db: Session,
    user: User,
    *,
    session_id: int,
    sku_id: int,
    planned_sales: int,
    planned_unit_price: Decimal | None,
    sort_order: int,
):
    session = _get_owned_session(db, user, session_id)
    sku = repo.get_sku(db, sku_id)
    if sku is None or sku.status != "active" or sku.product.status != "active":
        raise AppException(ErrorCode.NOT_FOUND, "可用 SKU 不存在")
    if session.status != "draft":
        raise AppException(ErrorCode.PARAM_ERROR, "只有草稿场次可以修改选品")
    if repo.get_session_item(db, session_id, sku_id) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "该 SKU 已在本场选品中")
    row = repo.create_session_item(
        db,
        session_id=session_id,
        sku_id=sku_id,
        planned_sales=planned_sales,
        planned_unit_price=planned_unit_price,
        sort_order=sort_order,
    )
    db.commit()
    db.refresh(row)
    return row


def create_package_draft(
    db: Session, user: User, *, session_id: int
) -> PreparationPackage:
    session = _get_owned_session(db, user, session_id)
    if not session.items:
        raise AppException(ErrorCode.PARAM_ERROR, "请先为场次添加商品")
    row = repo.create_package(
        db,
        session_id=session_id,
        version=repo.next_package_version(db, session_id),
        created_by=user.id,
    )
    db.commit()
    db.refresh(row)
    return row


def create_inventory_snapshot(
    db: Session,
    user: User,
    *,
    sku_id: int,
    available_quantity: int,
    reserved_quantity: int,
    source: str,
    captured_at: datetime | None,
) -> InventorySnapshot:
    _require_admin(user)
    if repo.get_sku(db, sku_id) is None:
        raise AppException(ErrorCode.NOT_FOUND, "SKU 不存在")
    if reserved_quantity > available_quantity:
        raise AppException(ErrorCode.PARAM_ERROR, "锁定库存不能大于可用库存")
    row = repo.create_inventory_snapshot(
        db,
        sku_id=sku_id,
        available_quantity=available_quantity,
        reserved_quantity=reserved_quantity,
        source=source.strip(),
        captured_at=captured_at or datetime.now(),
        created_by=user.id,
    )
    db.commit()
    db.refresh(row)
    return row


def list_latest_inventory(db: Session, *, sku_id: int | None = None) -> list[InventorySnapshot]:
    if sku_id is not None and repo.get_sku(db, sku_id) is None:
        raise AppException(ErrorCode.NOT_FOUND, "SKU 不存在")
    return repo.list_latest_inventory(db, sku_id)


def create_promotion(
    db: Session,
    user: User,
    *,
    name: str,
    promotion_type: str,
    sku_ids: list[int],
    fixed_price: Decimal | None,
    threshold_amount: Decimal | None,
    discount_amount: Decimal | None,
    stackable: bool,
    starts_at: datetime,
    ends_at: datetime,
) -> Promotion:
    _require_admin(user)
    if promotion_type not in PROMOTION_TYPES:
        raise AppException(ErrorCode.PARAM_ERROR, "不支持的促销类型")
    if starts_at >= ends_at:
        raise AppException(ErrorCode.PARAM_ERROR, "活动开始时间必须早于结束时间")
    unique_sku_ids = list(dict.fromkeys(sku_ids))
    if not unique_sku_ids:
        raise AppException(ErrorCode.PARAM_ERROR, "活动至少要关联一个 SKU")
    if any(repo.get_sku(db, sku_id) is None for sku_id in unique_sku_ids):
        raise AppException(ErrorCode.NOT_FOUND, "活动中包含不存在的 SKU")

    if promotion_type == PROMOTION_LIVE_PRICE:
        if fixed_price is None:
            raise AppException(ErrorCode.PARAM_ERROR, "直播价活动必须填写固定价格")
        threshold_amount = None
        discount_amount = None
    else:
        if discount_amount is None:
            raise AppException(ErrorCode.PARAM_ERROR, "满减或优惠券必须填写优惠金额")
        threshold_amount = threshold_amount or Decimal("0")
        fixed_price = None

    row = repo.create_promotion(
        db,
        sku_ids=unique_sku_ids,
        name=name.strip(),
        promotion_type=promotion_type,
        fixed_price=fixed_price,
        threshold_amount=threshold_amount,
        discount_amount=discount_amount,
        stackable=stackable,
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=user.id,
    )
    db.commit()
    db.refresh(row)
    return row


def list_promotions(db: Session, *, sku_id: int | None = None) -> list[Promotion]:
    return repo.list_promotions(db, sku_id)


_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return max(Decimal("0"), value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _discount_value(promotion: Promotion, basis: Decimal) -> Decimal | None:
    if promotion.promotion_type == PROMOTION_LIVE_PRICE:
        return None
    threshold = promotion.threshold_amount or Decimal("0")
    if basis < threshold:
        return None
    return promotion.discount_amount or Decimal("0")


def calculate_price(
    db: Session, *, sku_id: int, quantity: int, at_time: datetime | None = None
) -> dict:
    """确定性优惠计算：从有效活动中选出总价最低且满足叠加规则的方案。"""
    sku = repo.get_sku(db, sku_id)
    if sku is None or sku.status != "active":
        raise AppException(ErrorCode.NOT_FOUND, "可用 SKU 不存在")
    calculated_at = at_time or datetime.now()
    promotions = repo.list_active_promotions(db, sku_id, calculated_at)
    original = _money(sku.list_price * quantity)

    # 可叠加方案：每类只取最优惠的一项，防止两个同类券重复吃。
    stackable = [p for p in promotions if p.stackable]
    applied: list[tuple[Promotion, Decimal]] = []
    live = [p for p in stackable if p.promotion_type == PROMOTION_LIVE_PRICE and p.fixed_price]
    if live:
        chosen = min(live, key=lambda p: (p.fixed_price, p.id))
        subtotal = _money(chosen.fixed_price * quantity)
        applied.append((chosen, original - subtotal))
    else:
        subtotal = original
    eligibility_basis = subtotal

    for kind in (PROMOTION_THRESHOLD, PROMOTION_COUPON):
        candidates = []
        for promotion in stackable:
            if promotion.promotion_type != kind:
                continue
            amount = _discount_value(promotion, eligibility_basis)
            if amount is not None:
                candidates.append((promotion, amount))
        if candidates:
            chosen, amount = max(candidates, key=lambda item: (item[1], -item[0].id))
            subtotal = _money(subtotal - amount)
            applied.append((chosen, amount))

    if subtotal < original:
        best_total = subtotal
        best_applied = applied
    else:
        best_total = original
        best_applied = []

    # 不可叠加活动各自成为独立候选，与上面的可叠加组合比较，选最低总价。
    for promotion in (p for p in promotions if not p.stackable):
        if promotion.promotion_type == PROMOTION_LIVE_PRICE and promotion.fixed_price:
            candidate_total = _money(promotion.fixed_price * quantity)
            amount = original - candidate_total
        else:
            amount = _discount_value(promotion, original)
            if amount is None:
                continue
            candidate_total = _money(original - amount)
        if candidate_total < best_total:
            best_total = candidate_total
            best_applied = [(promotion, amount)]

    used_ids = {promotion.id for promotion, _ in best_applied}
    return {
        "sku_id": sku.id,
        "sku_code": sku.sku_code,
        "quantity": quantity,
        "unit_list_price": _money(sku.list_price),
        "original_total": original,
        "final_total": best_total,
        "final_unit_price": _money(best_total / quantity),
        "saved_amount": _money(original - best_total),
        "calculated_at": calculated_at,
        "applied_promotions": [
            {
                "promotion_id": promotion.id,
                "name": promotion.name,
                "promotion_type": promotion.promotion_type,
                "amount": _money(amount),
            }
            for promotion, amount in best_applied
        ],
        "ignored_promotion_ids": [p.id for p in promotions if p.id not in used_ids],
    }


INSPECTION_RULE_VERSION = "2026.10-v1"

DEFAULT_INSPECTION_RULES = {
    "SESSION_HAS_ITEMS": {"severity": SEVERITY_BLOCKER, "parameters": {}},
    "SKU_ACTIVE": {"severity": SEVERITY_BLOCKER, "parameters": {}},
    "INVENTORY_SUFFICIENT": {"severity": SEVERITY_BLOCKER, "parameters": {}},
    "INVENTORY_FRESH": {"severity": SEVERITY_WARNING, "parameters": {"max_age_hours": 24}},
    "PROMOTION_ACTIVE": {"severity": SEVERITY_WARNING, "parameters": {}},
    "PRICE_MATCH": {"severity": SEVERITY_BLOCKER, "parameters": {}},
    "PRODUCT_MANUAL_VALID": {"severity": SEVERITY_BLOCKER, "parameters": {}},
    "CLAIM_EVIDENCE_VALID": {"severity": SEVERITY_WARNING, "parameters": {}},
}


def list_inspection_rules(db: Session) -> list[InspectionRuleConfig]:
    return repo.list_inspection_rule_configs(db)


def update_inspection_rule(
    db: Session,
    user: User,
    *,
    rule_code: str,
    enabled: bool | None,
    severity: str | None,
    parameters: dict | None,
) -> InspectionRuleConfig:
    _require_admin(user)
    row = repo.get_inspection_rule_config(db, rule_code)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "巡检规则不存在")
    if enabled is not None:
        row.enabled = enabled
    if severity is not None:
        row.severity = severity
    if parameters is not None:
        allowed = {"max_age_hours"} if rule_code == "INVENTORY_FRESH" else set()
        unknown = set(parameters) - allowed
        if unknown:
            raise AppException(ErrorCode.PARAM_ERROR, f"不支持的规则参数: {', '.join(sorted(unknown))}")
        if "max_age_hours" in parameters:
            value = parameters["max_age_hours"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 168:
                raise AppException(ErrorCode.PARAM_ERROR, "库存时效必须是 1~168 小时的整数")
        row.parameters = parameters
    row.version += 1
    row.updated_by = user.id
    db.commit()
    db.refresh(row)
    return row


def _inspection_result(
    db: Session,
    *,
    batch_id: int,
    rule_code: str,
    rule_name: str,
    severity: str,
    passed: bool,
    message: str,
    sku_id: int | None = None,
    suggestion: str | None = None,
    evidence: dict | None = None,
    configs: dict[str, InspectionRuleConfig] | None = None,
):
    config = (configs or {}).get(rule_code)
    if config is not None:
        if not config.enabled:
            return None
        severity = config.severity
    return repo.create_inspection_result(
        db,
        batch_id=batch_id,
        rule_code=rule_code,
        rule_name=rule_name,
        severity=severity,
        status=RULE_PASSED if passed else RULE_FAILED,
        sku_id=sku_id,
        message=message,
        suggestion=None if passed else suggestion,
        evidence=evidence or {},
    )


def run_inspection(db: Session, user: User, *, session_id: int) -> InspectionBatch:
    """同步执行首版确定性巡检，并保存完整规则证据。"""
    session = _get_owned_session(db, user, session_id)
    started_at = datetime.now()
    session.status = SESSION_CHECKING
    configs = {row.rule_code: row for row in repo.list_inspection_rule_configs(db)}
    config_version = max((row.version for row in configs.values()), default=1)
    batch = repo.create_inspection_batch(
        db,
        session_id=session.id,
        status=INSPECTION_RUNNING,
        rule_version=f"config-v{config_version}",
        checked_by=user.id,
        started_at=started_at,
    )
    results = []

    has_items = bool(session.items)
    results.append(_inspection_result(
        db,
        batch_id=batch.id,
        rule_code="SESSION_HAS_ITEMS",
        rule_name="场次选品完整性",
        severity=SEVERITY_BLOCKER,
        passed=has_items,
        message=f"本场已选择 {len(session.items)} 个 SKU" if has_items else "本场尚未添加任何 SKU",
        suggestion="至少添加一个商品 SKU 后重新巡检",
        evidence={"item_count": len(session.items)},
        configs=configs,
    ))

    for item in session.items:
        sku = item.sku
        product = sku.product
        sku_active = sku.status == "active" and product.status == "active"
        results.append(_inspection_result(
            db,
            batch_id=batch.id,
            rule_code="SKU_ACTIVE",
            rule_name="商品与 SKU 状态",
            severity=SEVERITY_BLOCKER,
            passed=sku_active,
            sku_id=sku.id,
            message=(
                f"{sku.sku_code} 的商品与 SKU 均为启用状态"
                if sku_active else f"{sku.sku_code} 的商品或 SKU 已停用"
            ),
            suggestion="重新启用商品与 SKU，或将该 SKU 移出本场选品",
            evidence={"product_status": product.status, "sku_status": sku.status},
            configs=configs,
        ))

        snapshot = repo.get_latest_inventory(db, sku.id)
        if snapshot is None:
            inventory_ok = False
            inventory_message = f"{sku.sku_code} 没有库存快照"
            inventory_evidence = {"planned_sales": item.planned_sales}
        else:
            sellable = snapshot.sellable_quantity
            inventory_ok = sellable > 0 and (
                item.planned_sales == 0 or sellable >= item.planned_sales
            )
            inventory_message = (
                f"{sku.sku_code} 可售库存 {sellable}，满足计划销量 {item.planned_sales}"
                if inventory_ok
                else f"{sku.sku_code} 可售库存 {sellable}，不足以支持计划销量 {item.planned_sales}"
            )
            inventory_evidence = {
                "snapshot_id": snapshot.id,
                "available_quantity": snapshot.available_quantity,
                "reserved_quantity": snapshot.reserved_quantity,
                "sellable_quantity": sellable,
                "planned_sales": item.planned_sales,
                "captured_at": snapshot.captured_at.isoformat(),
                "source": snapshot.source,
            }
        results.append(_inspection_result(
            db,
            batch_id=batch.id,
            rule_code="INVENTORY_SUFFICIENT",
            rule_name="可售库存充足",
            severity=SEVERITY_BLOCKER,
            passed=inventory_ok,
            sku_id=sku.id,
            message=inventory_message,
            suggestion="重新采集库存、补充库存或降低本场计划销量",
            evidence=inventory_evidence,
            configs=configs,
        ))

        if snapshot is not None:
            fresh_config = configs.get("INVENTORY_FRESH")
            max_age_hours = int(
                (fresh_config.parameters if fresh_config else {}).get("max_age_hours", 24)
            )
            fresh = started_at - snapshot.captured_at <= timedelta(hours=max_age_hours)
            results.append(_inspection_result(
                db,
                batch_id=batch.id,
                rule_code="INVENTORY_FRESH",
                rule_name="库存快照时效",
                severity=SEVERITY_WARNING,
                passed=fresh,
                sku_id=sku.id,
                message=(
                    f"{sku.sku_code} 的库存快照在 {max_age_hours} 小时内"
                    if fresh else f"{sku.sku_code} 的库存快照已超过 {max_age_hours} 小时"
                ),
                suggestion="开播前重新采集平台库存",
                evidence={"snapshot_id": snapshot.id, "captured_at": snapshot.captured_at.isoformat(), "max_age_hours": max_age_hours},
                configs=configs,
            ))

        active_promotions = repo.list_active_promotions(db, sku.id, session.starts_at)
        results.append(_inspection_result(
            db,
            batch_id=batch.id,
            rule_code="PROMOTION_ACTIVE",
            rule_name="开播时活动有效",
            severity=SEVERITY_WARNING,
            passed=bool(active_promotions),
            sku_id=sku.id,
            message=(
                f"{sku.sku_code} 在计划开播时间有 {len(active_promotions)} 个有效活动"
                if active_promotions else f"{sku.sku_code} 在计划开播时间没有有效活动"
            ),
            suggestion="检查活动时间，或确认本场商品无需促销",
            evidence={"promotion_ids": [promotion.id for promotion in active_promotions]},
            configs=configs,
        ))

        if item.planned_unit_price is None:
            price_ok = False
            price_message = f"{sku.sku_code} 未设置计划到手价"
            price_evidence = {}
            price_severity = SEVERITY_WARNING
            price_suggestion = "为本场选品填写计划到手价后重新巡检"
        else:
            price = calculate_price(db, sku_id=sku.id, quantity=1, at_time=session.starts_at)
            actual = price["final_unit_price"]
            price_ok = actual == item.planned_unit_price
            price_message = (
                f"{sku.sku_code} 计算到手价 {actual} 与计划价格一致"
                if price_ok
                else f"{sku.sku_code} 计算到手价 {actual} 与计划价格 {item.planned_unit_price} 不一致"
            )
            price_evidence = {
                "planned_unit_price": str(item.planned_unit_price),
                "calculated_unit_price": str(actual),
                "promotion_ids": [x["promotion_id"] for x in price["applied_promotions"]],
            }
            price_severity = SEVERITY_BLOCKER
            price_suggestion = "核对平台活动配置或调整本场计划到手价"
        results.append(_inspection_result(
            db,
            batch_id=batch.id,
            rule_code="PRICE_MATCH",
            rule_name="计划到手价一致",
            severity=price_severity,
            passed=price_ok,
            sku_id=sku.id,
            message=price_message,
            suggestion=price_suggestion,
            evidence=price_evidence,
            configs=configs,
        ))

        for document_type, rule_code, rule_name, severity, suggestion in (
            (
                DOC_TYPE_PRODUCT_MANUAL,
                "PRODUCT_MANUAL_VALID",
                "商品说明资料有效",
                SEVERITY_BLOCKER,
                "上传已处理完成且覆盖开播时间的商品说明",
            ),
            (
                DOC_TYPE_CLAIM_EVIDENCE,
                "CLAIM_EVIDENCE_VALID",
                "宣传宣称依据有效",
                SEVERITY_WARNING,
                "补充质检报告、品牌授权或宣传宣称依据",
            ),
        ):
            valid, document_ids = repo.has_valid_document(
                db,
                product_id=product.id,
                sku_id=sku.id,
                document_type=document_type,
                effective_at=session.starts_at,
            )
            results.append(_inspection_result(
                db,
                batch_id=batch.id,
                rule_code=rule_code,
                rule_name=rule_name,
                severity=severity,
                passed=valid,
                sku_id=sku.id,
                message=(
                    f"{sku.sku_code} 找到 {len(document_ids)} 份有效{rule_name}"
                    if valid else f"{sku.sku_code} 缺少{rule_name}"
                ),
                suggestion=suggestion,
                evidence={"document_ids": document_ids, "document_type": document_type},
                configs=configs,
            ))

    # 被管理员停用的规则不会生成结果；在汇总前移除占位值。
    results = [result for result in results if result is not None]

    blocker_count = sum(
        result.status == RULE_FAILED and result.severity == SEVERITY_BLOCKER
        for result in results
    )
    warning_count = sum(
        result.status == RULE_FAILED and result.severity == SEVERITY_WARNING
        for result in results
    )
    passed_count = sum(result.status == RULE_PASSED for result in results)
    conclusion = INSPECTION_BLOCKED if blocker_count else INSPECTION_READY

    batch.status = INSPECTION_COMPLETED
    batch.conclusion = conclusion
    batch.score = max(0, 100 - blocker_count * 25 - warning_count * 8)
    batch.blocker_count = blocker_count
    batch.warning_count = warning_count
    batch.passed_count = passed_count
    batch.completed_at = datetime.now()
    session.status = SESSION_NEEDS_CONFIRMATION if blocker_count else SESSION_READY
    db.commit()
    return repo.get_inspection_batch(db, batch.id)


def list_inspections(
    db: Session, user: User, *, session_id: int
) -> list[InspectionBatch]:
    _get_owned_session(db, user, session_id)
    return repo.list_inspection_batches(db, session_id)


def get_inspection(db: Session, user: User, *, batch_id: int) -> InspectionBatch:
    batch = repo.get_inspection_batch(db, batch_id)
    if batch is None:
        raise AppException(ErrorCode.NOT_FOUND, "巡检批次不存在")
    _get_owned_session(db, user, batch.session_id)
    return batch


def get_session_recap(
    db: Session, user: User, *, session_id: int
) -> dict:
    """按当前数据库事实生成单场复盘；不调用模型，也不伪造订单/退款数据。"""
    session = _get_owned_session(db, user, session_id)
    generated_at = datetime.now()

    items = []
    planned_gmv = Decimal("0.00")
    for item in session.items:
        item_gmv = None
        if item.planned_unit_price is not None:
            item_gmv = (item.planned_unit_price * item.planned_sales).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            planned_gmv += item_gmv
        items.append({
            "sku_id": item.sku_id,
            "sku_code": item.sku.sku_code,
            "product_name": item.sku.product.name,
            "specification": item.sku.specification,
            "planned_sales": item.planned_sales,
            "planned_unit_price": item.planned_unit_price,
            "planned_gmv": item_gmv,
        })

    inspections = repo.list_inspection_batches(db, session_id)
    latest = inspections[0] if inspections else None
    unresolved_risks = (
        [result for result in latest.results if result.status == RULE_FAILED]
        if latest else []
    )

    tasks = rpa_repo.list_recap_tasks_for_session(db, session_id)
    terminal = [
        task for task in tasks
        if task.status in {TASK_SUCCEEDED, TASK_FAILED, TASK_QUEUE_FAILED}
    ]
    durations = [
        (task.completed_at - task.started_at).total_seconds()
        for task in terminal
        if task.started_at is not None and task.completed_at is not None
        and task.completed_at >= task.started_at
    ]
    task_rows = [
        {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "attempt_count": task.attempt_count,
            "duration_seconds": (
                round((task.completed_at - task.started_at).total_seconds(), 2)
                if task.started_at is not None and task.completed_at is not None
                and task.completed_at >= task.started_at else None
            ),
            "error_message": task.error_message,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
        }
        for task in tasks
    ]

    approvals = rpa_repo.list_recap_approvals_for_session(db, session_id)
    decision_durations = [
        (row.approved_at - row.created_at).total_seconds()
        for row in approvals
        if row.approved_at is not None and row.approved_at >= row.created_at
    ]
    approval_rows = [
        {
            "id": row.id,
            "task_type": row.task_type,
            "sku_code": row.sku.sku_code,
            "status": row.status,
            "reason": row.reason,
            "decision_note": row.decision_note,
            "decision_seconds": (
                round((row.approved_at - row.created_at).total_seconds(), 2)
                if row.approved_at is not None and row.approved_at >= row.created_at
                else None
            ),
            "task_id": row.task_id,
            "created_at": row.created_at,
            "approved_at": row.approved_at,
        }
        for row in approvals
    ]

    if latest is None:
        readiness = "not_inspected"
        headline = "本场尚未完成巡检，暂不能形成开播结论"
    elif latest.conclusion == INSPECTION_BLOCKED:
        readiness = "blocked"
        headline = f"最新巡检仍有 {latest.blocker_count} 个阻断项需要处理"
    else:
        readiness = "ready"
        open_approvals = sum(
            row.status in {APPROVAL_PENDING, APPROVAL_APPROVED} for row in approvals
        )
        headline = (
            f"最新巡检已通过，但仍有 {open_approvals} 个审批流程未闭环"
            if open_approvals else "最新巡检已通过，当前具备开播条件"
        )

    succeeded = sum(task.status == TASK_SUCCEEDED for task in tasks)
    failed = sum(task.status in {TASK_FAILED, TASK_QUEUE_FAILED} for task in tasks)
    return {
        "session_id": session.id,
        "title": session.title,
        "status": session.status,
        "starts_at": session.starts_at,
        "owner_id": session.owner_id,
        "generated_at": generated_at,
        "headline": headline,
        "readiness": readiness,
        "sku_count": len(items),
        "planned_sales": sum(item.planned_sales for item in session.items),
        "planned_gmv": planned_gmv,
        "items": items,
        "inspection": {
            "total_runs": len(inspections),
            "latest": latest,
            "unresolved_risks": unresolved_risks,
        },
        "automation": {
            "total": len(tasks),
            "queued": sum(task.status == TASK_QUEUED for task in tasks),
            "running": sum(task.status == TASK_RUNNING for task in tasks),
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": (
                round(succeeded / len(terminal) * 100, 2) if terminal else None
            ),
            "avg_duration_seconds": (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
            "retry_task_count": sum(task.attempt_count > 1 for task in tasks),
            "tasks": task_rows,
        },
        "approvals": {
            "total": len(approvals),
            "pending": sum(row.status == APPROVAL_PENDING for row in approvals),
            "approved": sum(row.status == APPROVAL_APPROVED for row in approvals),
            "rejected": sum(row.status == APPROVAL_REJECTED for row in approvals),
            "executed": sum(row.status == APPROVAL_EXECUTED for row in approvals),
            "failed": sum(row.status == APPROVAL_FAILED for row in approvals),
            "avg_decision_seconds": (
                round(sum(decision_durations) / len(decision_durations), 2)
                if decision_durations else None
            ),
            "approvals": approval_rows,
        },
    }

