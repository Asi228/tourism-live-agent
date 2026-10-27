"""初始化一套可直接用于作品展示的旅游直播电商数据。

数据覆盖：演示账号、三个知识库、十份业务文档、商品与 SKU、库存活动、
三个不同状态的直播场次、巡检结果、RPA 执行记录和审批记录。

脚本只删除带固定演示标识的数据，不会清理用户自行创建的业务数据。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401
from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.models.document import (  # noqa: E402
    DOC_TYPE_AFTER_SALES,
    DOC_TYPE_CLAIM_EVIDENCE,
    DOC_TYPE_GENERAL,
    DOC_TYPE_LIVE_POLICY,
    DOC_TYPE_PRODUCT_MANUAL,
    Document,
)
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.live_commerce import (  # noqa: E402
    LiveSession,
    LiveSessionItem,
    Product,
    ProductSku,
    Promotion,
    PromotionSku,
    SESSION_FINISHED,
)
from app.models.role import Role  # noqa: E402
from app.models.rpa import (  # noqa: E402
    APPROVAL_EXECUTED,
    APPROVAL_PENDING,
    TASK_COLLECT,
    TASK_PRICE_FIX,
    TASK_SUCCEEDED,
    MockPlatformSku,
    MonitoringPlan,
    RpaApproval,
    RpaTask,
    RpaTaskStep,
)
from app.models.user import User  # noqa: E402
from app.repositories import document_repository  # noqa: E402
from app.repositories import user_repository  # noqa: E402
from app.schemas.knowledge import KnowledgeBaseCreate  # noqa: E402
from app.services import (  # noqa: E402
    document_service,
    knowledge_service,
    live_commerce_service,
    rpa_service,
    vector_service,
)


DEMO_USERNAME = "demo_operator"
DEMO_EMAIL = "demo_operator@example.local"
DEMO_PASSWORD = "Demo@123456"
DEMO_SESSION_TITLES = (
    "周末亲子景区门票专场",
    "三亚酒店套餐特惠专场",
    "城市周边游回顾专场",
)
DEMO_KBS = {
    "旅游产品资料": "景区、酒店和度假套餐的使用规则、退改条款与宣传依据。",
    "直播运营规范": "开播检查、价格审批、风险治理和 RPA 执行规范。",
    "客服与售后SOP": "旅游订单出票、核销、退改和异常升级的标准处理流程。",
}
DEMO_SKU_CODES = (
    "TRIP-PARK-ADULT",
    "TRIP-PARK-FAMILY",
    "TRIP-OCEAN-PAIR",
    "TRIP-HOTEL-SANYA-2N",
    "TRIP-HOTEL-SANYA-3N",
    "TRIP-SPA-FAMILY",
    "TRIP-RESORT-2D1N",
    "TRIP-MUSEUM-FAMILY",
)
LEGACY_SKU_CODES = ("YL-SH-450", "DEMO-RPA-001")


PRODUCTS = (
    ("欢乐谷主题乐园门票", "城市玩乐", "景区门票", (
        ("TRIP-PARK-ADULT", "成人全天票", "229.00"),
        ("TRIP-PARK-FAMILY", "2大1小亲子家庭票", "599.00"),
    )),
    ("海洋世界双人票", "蓝湾文旅", "景区门票", (
        ("TRIP-OCEAN-PAIR", "双人全天票", "399.00"),
    )),
    ("三亚海景酒店套餐", "海屿度假", "酒店套餐", (
        ("TRIP-HOTEL-SANYA-2N", "豪华海景房2晚含双早", "2699.00"),
        ("TRIP-HOTEL-SANYA-3N", "豪华海景房3晚含双早", "3699.00"),
    )),
    ("山谷温泉亲子票", "山谷温泉", "休闲度假", (
        ("TRIP-SPA-FAMILY", "2大1小温泉票", "498.00"),
    )),
    ("湖畔度假村套餐", "湖畔假日", "酒店套餐", (
        ("TRIP-RESORT-2D1N", "园景房2天1晚含早餐", "899.00"),
    )),
    ("自然博物馆亲子票", "城市文博", "文博场馆", (
        ("TRIP-MUSEUM-FAMILY", "2大1小家庭票", "168.00"),
    )),
)


DOCUMENTS = (
    ("旅游产品资料", "01_欢乐谷门票使用与入园规则.md", DOC_TYPE_PRODUCT_MANUAL, "TRIP-PARK-ADULT"),
    ("旅游产品资料", "02_欢乐谷直播宣传依据.md", DOC_TYPE_CLAIM_EVIDENCE, "TRIP-PARK-ADULT"),
    ("旅游产品资料", "03_海洋世界双人票使用规则.md", DOC_TYPE_PRODUCT_MANUAL, "TRIP-OCEAN-PAIR"),
    ("旅游产品资料", "04_海洋世界直播宣传依据.md", DOC_TYPE_CLAIM_EVIDENCE, "TRIP-OCEAN-PAIR"),
    ("旅游产品资料", "05_三亚海景酒店套餐规则.md", DOC_TYPE_PRODUCT_MANUAL, "TRIP-HOTEL-SANYA-2N"),
    ("旅游产品资料", "06_三亚酒店直播宣传依据.md", DOC_TYPE_CLAIM_EVIDENCE, "TRIP-HOTEL-SANYA-2N"),
    ("旅游产品资料", "07_酒店周末节假日加价规则.md", DOC_TYPE_GENERAL, None),
    ("客服与售后SOP", "08_旅游产品退改异常处理SOP.md", DOC_TYPE_AFTER_SALES, None),
    ("直播运营规范", "09_直播开播检查与价格审批规范.md", DOC_TYPE_LIVE_POLICY, None),
    ("客服与售后SOP", "10_景区门票客服标准话术.md", DOC_TYPE_AFTER_SALES, None),
)


def _purge_vectors(document_ids: list[int]) -> None:
    if not document_ids:
        return
    try:
        from app.rag import vector_store

        for document_id in document_ids:
            vector_store.delete_by_document(document_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Milvus 暂不可用，旧演示向量稍后会被覆盖：{exc}")


def _delete_sessions(db, session_ids: list[int]) -> None:
    if not session_ids:
        return
    task_ids = list(db.scalars(select(RpaTask.id).where(RpaTask.session_id.in_(session_ids))))
    db.execute(delete(RpaApproval).where(RpaApproval.session_id.in_(session_ids)))
    db.execute(delete(MonitoringPlan).where(MonitoringPlan.session_id.in_(session_ids)))
    if task_ids:
        db.execute(delete(RpaTaskStep).where(RpaTaskStep.task_id.in_(task_ids)))
        db.execute(delete(RpaTask).where(RpaTask.id.in_(task_ids)))
    db.execute(delete(LiveSession).where(LiveSession.id.in_(session_ids)))


def _reset_demo(db, *, remove_legacy: bool) -> None:
    kb_rows = list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.name.in_(DEMO_KBS))))
    doc_ids = list(
        db.scalars(
            select(Document.id).where(Document.knowledge_base_id.in_([row.id for row in kb_rows]))
        )
    ) if kb_rows else []
    _purge_vectors(doc_ids)
    for kb in kb_rows:
        upload_dir = Path(settings.UPLOAD_DIR) / str(kb.id)
        db.delete(kb)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)

    codes = list(DEMO_SKU_CODES) + (list(LEGACY_SKU_CODES) if remove_legacy else [])
    sku_rows = list(db.scalars(select(ProductSku).where(ProductSku.sku_code.in_(codes))))
    sku_ids = [row.id for row in sku_rows]
    session_ids = set(
        db.scalars(select(LiveSession.id).where(LiveSession.title.in_(DEMO_SESSION_TITLES)))
    )
    if sku_ids:
        session_ids.update(
            db.scalars(select(LiveSessionItem.session_id).where(LiveSessionItem.sku_id.in_(sku_ids)))
        )
    _delete_sessions(db, list(session_ids))

    if sku_ids:
        promotion_ids = list(
            db.scalars(select(PromotionSku.promotion_id).where(PromotionSku.sku_id.in_(sku_ids)))
        )
        db.execute(delete(MockPlatformSku).where(MockPlatformSku.sku_code.in_(codes)))
        if promotion_ids:
            db.execute(delete(PromotionSku).where(PromotionSku.promotion_id.in_(promotion_ids)))
            db.execute(delete(Promotion).where(Promotion.id.in_(promotion_ids)))
        product_ids = {row.product_id for row in sku_rows}
        db.execute(delete(Product).where(Product.id.in_(product_ids)))
    db.commit()


def _get_admin(db) -> User:
    admin = db.scalar(
        select(User).join(Role).where(Role.code == "admin", User.is_active.is_(True)).limit(1)
    )
    if admin is None:
        raise RuntimeError("没有可用管理员，请先在 .env 配置 ADMIN_USERNAME/ADMIN_PASSWORD 并启动一次 API")
    return admin


def _upsert_demo_user(db) -> User:
    employee_role = db.scalar(select(Role).where(Role.code == "employee"))
    if employee_role is None:
        raise RuntimeError("employee 角色不存在，请先执行数据库初始化")
    user = user_repository.get_by_username(db, DEMO_USERNAME)
    if user is None:
        user = user_repository.create(
            db,
            username=DEMO_USERNAME,
            email=DEMO_EMAIL,
            password_hash=hash_password(DEMO_PASSWORD),
            role_id=employee_role.id,
        )
    else:
        user.email = DEMO_EMAIL
        user.password_hash = hash_password(DEMO_PASSWORD)
        user.role_id = employee_role.id
        user.is_active = True
    db.commit()
    db.refresh(user)
    return user


def _create_catalog(db, admin: User) -> dict[str, ProductSku]:
    skus: dict[str, ProductSku] = {}
    for product_name, brand, category, sku_defs in PRODUCTS:
        product = live_commerce_service.create_product(
            db, admin, name=product_name, brand=brand, category=category
        )
        for code, specification, price in sku_defs:
            skus[code] = live_commerce_service.create_sku(
                db,
                admin,
                product_id=product.id,
                sku_code=code,
                specification=specification,
                list_price=Decimal(price),
                currency="CNY",
            )
    return skus


def _create_knowledge(db, admin: User, skus: dict[str, ProductSku]) -> tuple[dict[str, KnowledgeBase], list[int]]:
    kbs = {
        name: knowledge_service.create_kb(
            db,
            KnowledgeBaseCreate(name=name, description=description, is_public=True),
            admin,
        )
        for name, description in DEMO_KBS.items()
    }
    source_dir = ROOT / "docs" / "demo" / "tourism"
    document_ids: list[int] = []
    effective_from = datetime.now() - timedelta(days=365)
    effective_to = datetime.now() + timedelta(days=730)

    for kb_name, filename, doc_type, sku_code in DOCUMENTS:
        source = source_dir / filename
        if not source.exists():
            raise RuntimeError(f"缺少演示文档：{source}")
        kb = kbs[kb_name]
        stored_name = f"{uuid4().hex}.md"
        target_dir = Path(settings.UPLOAD_DIR) / str(kb.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / stored_name
        shutil.copy2(source, target)
        sku = skus.get(sku_code) if sku_code else None
        doc = document_repository.create(
            db,
            knowledge_base_id=kb.id,
            uploader_id=admin.id,
            filename=filename,
            stored_name=stored_name,
            file_path=str(target.resolve()),
            file_type="md",
            file_size=target.stat().st_size,
            product_id=sku.product_id if sku else None,
            # 产品规则对该商品的全部规格生效；巡检时 family/成人票都能复用同一依据。
            sku_id=None,
            document_type=doc_type,
            version="2026.10-demo",
            effective_from=effective_from,
            effective_to=effective_to,
        )
        db.commit()
        document_ids.append(doc.id)
        document_service.process_document(doc.id)

    # process_document 使用独立 Session。MySQL 默认 REPEATABLE READ 下，复用当前
    # Session 可能仍看到处理前的快照，因此用一个全新事务复核最终状态。
    db.commit()
    db.expire_all()
    with new_session() as verify_db:
        failed = list(
            verify_db.scalars(
                select(Document).where(
                    Document.id.in_(document_ids), Document.status != "completed"
                )
            )
        )
    if failed:
        details = ", ".join(f"{row.filename}: {row.error_msg}" for row in failed)
        raise RuntimeError(f"演示文档处理失败：{details}")
    return kbs, document_ids


def _seed_inventory_promotions(db, admin: User, skus: dict[str, ProductSku], now: datetime) -> None:
    inventory = {
        "TRIP-PARK-ADULT": (800, 80),
        "TRIP-PARK-FAMILY": (260, 20),
        "TRIP-OCEAN-PAIR": (350, 25),
        "TRIP-HOTEL-SANYA-2N": (18, 10),
        "TRIP-HOTEL-SANYA-3N": (25, 5),
        "TRIP-SPA-FAMILY": (160, 10),
        "TRIP-RESORT-2D1N": (90, 8),
        "TRIP-MUSEUM-FAMILY": (500, 20),
    }
    live_prices = {
        "TRIP-PARK-ADULT": "169.00",
        "TRIP-PARK-FAMILY": "459.00",
        "TRIP-OCEAN-PAIR": "299.00",
        "TRIP-HOTEL-SANYA-2N": "2199.00",
    }
    for code, sku in skus.items():
        available, reserved = inventory[code]
        live_commerce_service.create_inventory_snapshot(
            db,
            admin,
            sku_id=sku.id,
            available_quantity=available,
            reserved_quantity=reserved,
            source="demo_rpa",
            captured_at=now - timedelta(minutes=15),
        )
        if code in live_prices:
            live_commerce_service.create_promotion(
                db,
                admin,
                name=f"[演示] {code} 直播专享价",
                promotion_type="live_price",
                sku_ids=[sku.id],
                fixed_price=Decimal(live_prices[code]),
                threshold_amount=None,
                discount_amount=None,
                stackable=False,
                starts_at=now - timedelta(days=30),
                ends_at=now + timedelta(days=30),
            )
        rpa_service.save_mock_sku(
            db,
            admin,
            row_id=None,
            sku_code=code,
            title=f"{sku.product.name} {sku.specification}",
            platform_status="active",
            list_price=sku.list_price,
            live_price=Decimal(live_prices[code]) if code in live_prices else None,
            available_quantity=available,
            reserved_quantity=reserved,
            promotion_starts_at=now - timedelta(days=30) if code in live_prices else None,
            promotion_ends_at=now + timedelta(days=30) if code in live_prices else None,
            stackable=False,
        )


def _make_session(db, owner: User, *, title: str, starts_at: datetime, items: list[tuple[ProductSku, int, str]]) -> LiveSession:
    session = live_commerce_service.create_session(db, owner, title=title, starts_at=starts_at)
    for index, (sku, planned_sales, planned_price) in enumerate(items, start=1):
        live_commerce_service.add_session_item(
            db,
            owner,
            session_id=session.id,
            sku_id=sku.id,
            planned_sales=planned_sales,
            planned_unit_price=Decimal(planned_price),
            sort_order=index,
        )
    package = live_commerce_service.create_package_draft(db, owner, session_id=session.id)
    package.content = (
        f"# {title}开播准备包\n\n"
        "- 已完成商品资料、库存、活动价与宣传依据核验。\n"
        "- 主播按知识库口径讲解，退改和预约条件必须完整展示。\n"
        "- 出现价格或库存异常时暂停上链接，转运营复核。"
    )
    db.commit()
    return session


def _create_workflows(db, admin: User, owner: User, skus: dict[str, ProductSku], now: datetime) -> dict[str, LiveSession]:
    ready = _make_session(
        db,
        owner,
        title=DEMO_SESSION_TITLES[0],
        starts_at=now + timedelta(days=2),
        items=[
            (skus["TRIP-PARK-ADULT"], 300, "169.00"),
            (skus["TRIP-PARK-FAMILY"], 120, "459.00"),
        ],
    )
    ready_batch = live_commerce_service.run_inspection(db, owner, session_id=ready.id)
    if ready_batch.conclusion != "ready":
        raise RuntimeError(f"亲子景区场次预期为 ready，实际为 {ready_batch.conclusion}")

    blocked = _make_session(
        db,
        owner,
        title=DEMO_SESSION_TITLES[1],
        starts_at=now + timedelta(days=5),
        items=[(skus["TRIP-HOTEL-SANYA-2N"], 30, "1999.00")],
    )
    blocked_batch = live_commerce_service.run_inspection(db, owner, session_id=blocked.id)
    if blocked_batch.conclusion != "blocked":
        raise RuntimeError(f"酒店场次预期为 blocked，实际为 {blocked_batch.conclusion}")

    finished = _make_session(
        db,
        owner,
        title=DEMO_SESSION_TITLES[2],
        starts_at=now - timedelta(days=7),
        items=[(skus["TRIP-OCEAN-PAIR"], 160, "299.00")],
    )
    finished_batch = live_commerce_service.run_inspection(db, owner, session_id=finished.id)
    if finished_batch.conclusion != "ready":
        raise RuntimeError(f"回顾场次预期巡检通过，实际为 {finished_batch.conclusion}")
    finished.status = SESSION_FINISHED

    price_result = next(
        row for row in blocked_batch.results
        if row.rule_code == "PRICE_MATCH" and row.status == "failed"
    )
    db.add(RpaApproval(
        session_id=blocked.id,
        task_type=TASK_PRICE_FIX,
        sku_id=skus["TRIP-HOTEL-SANYA-2N"].id,
        expected_value={"live_price": "1999.00"},
        idempotency_key="demo-tourism-pending-price-fix",
        status=APPROVAL_PENDING,
        # 由另一名员工（这里复用管理员种子账号）发起，场次负责人登录演示账号后
        # 可以真实体验“四眼原则”下的审批；自己新建的申请仍不能自批。
        requested_by=admin.id,
        reason="平台直播价 2199 元与场次计划价 1999 元不一致，请审批改价。",
        source_inspection_result_id=price_result.id,
        source_snapshot={"planned": "1999.00", "platform": "2199.00", "risk": price_result.message},
    ))

    task = RpaTask(
        session_id=finished.id,
        task_type=TASK_COLLECT,
        status=TASK_SUCCEEDED,
        idempotency_key="demo-tourism-finished-collect",
        requested_by=owner.id,
        payload={"session_id": finished.id, "source": "mock_platform"},
        result={
            "collected_skus": 1,
            "inspection": {"conclusion": "ready", "score": finished_batch.score, "blocker_count": 0},
        },
        attempt_count=1,
        started_at=now - timedelta(days=7, minutes=-3),
        completed_at=now - timedelta(days=7, minutes=-4),
    )
    db.add(task)
    db.flush()
    for index, (code, name, message) in enumerate((
        ("login", "登录平台", "使用受控演示账号登录成功"),
        ("collect", "采集商品", "读取价格、库存和活动有效期"),
        ("persist", "快照落库", "结构化快照已保存"),
        ("inspect", "自动巡检", "巡检通过，场次可开播"),
    )):
        step_start = now - timedelta(days=7) + timedelta(seconds=index * 4)
        db.add(RpaTaskStep(
            task_id=task.id,
            step_code=code,
            step_name=name,
            status="succeeded",
            message=message,
            screenshot_path=None,
            started_at=step_start,
            completed_at=step_start + timedelta(seconds=3),
        ))

    executed = RpaTask(
        session_id=finished.id,
        task_type=TASK_PRICE_FIX,
        status=TASK_SUCCEEDED,
        idempotency_key="demo-tourism-executed-price-fix",
        requested_by=admin.id,
        payload={"sku_code": "TRIP-OCEAN-PAIR", "expected": {"live_price": "299.00"}},
        result={"before": {"live_price": "319.00"}, "after": {"live_price": "299.00"}, "verified": True},
        attempt_count=1,
        started_at=now - timedelta(days=8, minutes=-5),
        completed_at=now - timedelta(days=8, minutes=-6),
    )
    db.add(executed)
    db.flush()
    db.add(RpaApproval(
        session_id=finished.id,
        task_type=TASK_PRICE_FIX,
        sku_id=skus["TRIP-OCEAN-PAIR"].id,
        expected_value={"live_price": "299.00"},
        idempotency_key="demo-tourism-executed-price-fix",
        status=APPROVAL_EXECUTED,
        requested_by=owner.id,
        reason="预热巡检发现价格偏差，申请同步本场活动价。",
        approved_by=admin.id,
        approved_at=now - timedelta(days=8, minutes=-4),
        decision_note="活动方案与供应商确认单一致，同意执行。",
        task_id=executed.id,
        source_snapshot={"before": "319.00", "target": "299.00"},
    ))
    db.commit()
    return {"ready": ready, "blocked": blocked, "finished": finished}


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化旅游直播电商作品展示数据")
    parser.add_argument("--reset", action="store_true", help="删除并重建固定标识的旅游演示数据")
    parser.add_argument("--remove-legacy", action="store_true", help="同时删除旧洗护演示商品与关联场次")
    parser.add_argument("--skip-vectors", action="store_true", help="跳过 Milvus 向量化，仅用于外部服务不可用时")
    args = parser.parse_args()

    now = datetime.now()
    with new_session() as db:
        admin = _get_admin(db)
        exists = db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.name.in_(DEMO_KBS)))
        if exists and not args.reset:
            print("[FAIL] 旅游演示数据已经存在；需要重建时请加 --reset。")
            return 2
        if args.reset or args.remove_legacy:
            print("[1/6] 清理固定标识的旧演示数据……")
            _reset_demo(db, remove_legacy=args.remove_legacy)
        owner = _upsert_demo_user(db)
        print(f"[2/6] 演示账号就绪：{DEMO_USERNAME}")

        skus = _create_catalog(db, admin)
        print(f"[3/6] 商品主档就绪：{len(PRODUCTS)} 个商品，{len(skus)} 个 SKU")

        kbs, document_ids = _create_knowledge(db, admin, skus)
        print(f"[4/6] 知识库就绪：{len(kbs)} 个知识库，{len(document_ids)} 份已切分文档")

        if not args.skip_vectors:
            total_vectors = 0
            for kb in kbs.values():
                result = vector_service.vectorize_kb(db, admin, kb.id)
                total_vectors += result["vectorized"]
            print(f"      Milvus 向量写入完成：{total_vectors} 个分块")
        else:
            print("      [WARN] 已按参数跳过向量化，知识问答暂不能走向量召回")

        _seed_inventory_promotions(db, admin, skus, now)
        sessions = _create_workflows(db, admin, owner, skus, now)
        print("[5/6] 直播流程就绪：可开播、待处理、已结束场次各 1 个")

        counts = {
            "knowledge_bases": len(kbs),
            "documents": len(document_ids),
            "products": len(PRODUCTS),
            "skus": len(skus),
            "sessions": len(sessions),
        }
        print(f"[6/6] 初始化完成：{counts}")
        print(f"演示登录：{DEMO_USERNAME} / {DEMO_PASSWORD}")
        print("建议流程：运营工作台 → 直播任务 → 运营 Agent → 风险与审批 → 自动化任务 → 知识问答")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
