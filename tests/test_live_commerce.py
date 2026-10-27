"""直播电商业务骨架：商品、场次、选品与准备包版本。"""

from app.models import Document, InspectionRuleConfig, KnowledgeBase
from app.models.document import (
    DOC_TYPE_CLAIM_EVIDENCE,
    DOC_TYPE_PRODUCT_MANUAL,
    STATUS_COMPLETED,
)
from app.core.response import ErrorCode


def _seed_rule(session_factory, **overrides):
    fields = {
        "rule_code": "SESSION_HAS_ITEMS",
        "rule_name": "场次选品完整性",
        "description": "场次至少包含一个可售 SKU",
        "enabled": True,
        "severity": "blocker",
        "parameters": {},
    }
    fields.update(overrides)
    with session_factory() as db:
        db.add(InspectionRuleConfig(**fields))
        db.commit()


def _create_product_and_sku(client, admin):
    product = client.post(
        "/api/commerce/products",
        json={"name": "山茶花修护洗发水", "brand": "云岚", "category": "洗护"},
        headers=admin["headers"],
    ).json()
    assert product["code"] == 0, product
    sku = client.post(
        f"/api/commerce/products/{product['data']['id']}/skus",
        json={
            "sku_code": "YL-SH-450",
            "specification": "450ml",
            "list_price": "129.00",
        },
        headers=admin["headers"],
    ).json()
    assert sku["code"] == 0, sku
    return product["data"], sku["data"]


def test_admin_builds_product_catalog(client, make_user):
    admin = make_user("admin")
    product, sku = _create_product_and_sku(client, admin)

    rows = client.get("/api/commerce/products", headers=admin["headers"]).json()
    assert rows["code"] == 0
    assert rows["data"][0]["name"] == "山茶花修护洗发水"
    assert rows["data"][0]["skus"][0]["sku_code"] == "YL-SH-450"
    assert sku["product_id"] == product["id"]


def test_employee_cannot_change_product_catalog(client, make_user):
    employee = make_user("employee")
    result = client.post(
        "/api/commerce/products",
        json={"name": "越权商品"},
        headers=employee["headers"],
    ).json()
    assert result["code"] == ErrorCode.FORBIDDEN


def test_admin_configures_inspection_rule_and_version(client, make_user, session_factory):
    admin = make_user("admin")
    employee = make_user("employee")
    _seed_rule(session_factory)

    denied = client.patch(
        "/api/commerce/inspection-rules/SESSION_HAS_ITEMS",
        json={"severity": "warning"},
        headers=employee["headers"],
    ).json()
    assert denied["code"] == ErrorCode.FORBIDDEN

    updated = client.patch(
        "/api/commerce/inspection-rules/SESSION_HAS_ITEMS",
        json={"enabled": False, "severity": "warning"},
        headers=admin["headers"],
    ).json()
    assert updated["code"] == 0
    assert updated["data"]["enabled"] is False
    assert updated["data"]["severity"] == "warning"
    assert updated["data"]["version"] == 2


def test_disabled_rule_is_not_written_to_inspection(client, make_user, session_factory):
    owner = make_user("employee")
    _seed_rule(session_factory, enabled=False)
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "空场次", "starts_at": "2026-11-01T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]

    result = client.post(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=owner["headers"],
    ).json()
    assert result["code"] == 0
    assert result["data"]["conclusion"] == "ready"
    assert result["data"]["results"] == []
    assert result["data"]["rule_version"] == "config-v1"


def test_session_owner_can_add_item_and_create_versioned_packages(client, make_user):
    admin = make_user("admin")
    employee = make_user("employee")
    _, sku = _create_product_and_sku(client, admin)

    session = client.post(
        "/api/commerce/sessions",
        json={"title": "双十一洗护专场", "starts_at": "2026-11-11T20:00:00"},
        headers=employee["headers"],
    ).json()
    assert session["code"] == 0, session
    session_id = session["data"]["id"]

    item = client.post(
        f"/api/commerce/sessions/{session_id}/items",
        json={"sku_id": sku["id"], "planned_sales": 800, "sort_order": 1},
        headers=employee["headers"],
    ).json()
    assert item["code"] == 0, item
    assert item["data"]["sku"]["specification"] == "450ml"

    first = client.post(
        f"/api/commerce/sessions/{session_id}/packages",
        headers=employee["headers"],
    ).json()
    second = client.post(
        f"/api/commerce/sessions/{session_id}/packages",
        headers=employee["headers"],
    ).json()
    assert first["data"]["version"] == 1
    assert second["data"]["version"] == 2

    detail = client.get(
        f"/api/commerce/sessions/{session_id}", headers=employee["headers"]
    ).json()
    assert len(detail["data"]["items"]) == 1
    assert [x["version"] for x in detail["data"]["packages"]] == [1, 2]


def test_employee_cannot_read_or_modify_someone_elses_session(client, make_user):
    owner = make_user("employee")
    stranger = make_user("employee")
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "私有场次", "starts_at": "2026-11-01T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]

    result = client.get(
        f"/api/commerce/sessions/{session['id']}", headers=stranger["headers"]
    ).json()
    assert result["code"] == ErrorCode.NOT_FOUND

    own_list = client.get("/api/commerce/sessions", headers=stranger["headers"]).json()
    assert own_list["data"] == []


def test_package_requires_at_least_one_item(client, make_user):
    employee = make_user("employee")
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "空场次", "starts_at": "2026-11-01T20:00:00"},
        headers=employee["headers"],
    ).json()["data"]
    result = client.post(
        f"/api/commerce/sessions/{session['id']}/packages",
        headers=employee["headers"],
    ).json()
    assert result["code"] == ErrorCode.PARAM_ERROR
    assert "添加商品" in result["message"]


def test_inventory_keeps_history_but_returns_latest_snapshot(client, make_user):
    admin = make_user("admin")
    _, sku = _create_product_and_sku(client, admin)

    older = client.post(
        "/api/commerce/inventory",
        json={
            "sku_id": sku["id"], "available_quantity": 1000, "reserved_quantity": 100,
            "source": "mock-erp", "captured_at": "2026-10-21T10:00:00",
        },
        headers=admin["headers"],
    ).json()
    assert older["code"] == 0, older
    latest = client.post(
        "/api/commerce/inventory",
        json={
            "sku_id": sku["id"], "available_quantity": 600, "reserved_quantity": 80,
            "source": "mock-erp", "captured_at": "2026-10-21T11:00:00",
        },
        headers=admin["headers"],
    ).json()
    assert latest["code"] == 0, latest

    rows = client.get(
        "/api/commerce/inventory", params={"sku_id": sku["id"]}, headers=admin["headers"]
    ).json()["data"]
    assert len(rows) == 1
    assert rows[0]["available_quantity"] == 600
    assert rows[0]["sellable_quantity"] == 520


def test_inventory_rejects_reserved_above_available(client, make_user):
    admin = make_user("admin")
    _, sku = _create_product_and_sku(client, admin)
    result = client.post(
        "/api/commerce/inventory",
        json={"sku_id": sku["id"], "available_quantity": 10, "reserved_quantity": 11},
        headers=admin["headers"],
    ).json()
    assert result["code"] == ErrorCode.PARAM_ERROR


def _promotion(client, headers, sku_id, **overrides):
    payload = {
        "name": "默认活动",
        "promotion_type": "coupon",
        "sku_ids": [sku_id],
        "threshold_amount": "0.00",
        "discount_amount": "10.00",
        "stackable": True,
        "starts_at": "2026-10-01T00:00:00",
        "ends_at": "2026-12-31T23:59:59",
    }
    payload.update(overrides)
    body = client.post("/api/commerce/promotions", json=payload, headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def test_deterministic_price_engine_chooses_lowest_valid_plan(client, make_user):
    admin = make_user("admin")
    _, sku = _create_product_and_sku(client, admin)
    _promotion(
        client, admin["headers"], sku["id"],
        name="直播价 99", promotion_type="live_price", fixed_price="99.00",
        threshold_amount=None, discount_amount=None, stackable=True,
    )
    _promotion(
        client, admin["headers"], sku["id"],
        name="满 150 减 20", promotion_type="threshold_discount",
        threshold_amount="150.00", discount_amount="20.00", stackable=True,
    )
    _promotion(client, admin["headers"], sku["id"], name="叠加券 10", stackable=True)
    strongest = _promotion(
        client, admin["headers"], sku["id"], name="不可叠加券 120",
        discount_amount="120.00", stackable=False,
    )

    result = client.post(
        "/api/commerce/price-preview",
        json={"sku_id": sku["id"], "quantity": 2, "at_time": "2026-11-11T20:00:00"},
        headers=admin["headers"],
    ).json()
    assert result["code"] == 0, result
    data = result["data"]
    assert data["original_total"] == "258.00"
    assert data["final_total"] == "138.00"
    assert data["saved_amount"] == "120.00"
    assert [p["promotion_id"] for p in data["applied_promotions"]] == [strongest["id"]]
    assert len(data["ignored_promotion_ids"]) == 3


def test_price_engine_stacks_one_best_promotion_per_type(client, make_user):
    admin = make_user("admin")
    _, sku = _create_product_and_sku(client, admin)
    _promotion(
        client, admin["headers"], sku["id"],
        name="直播价 99", promotion_type="live_price", fixed_price="99.00",
        threshold_amount=None, discount_amount=None, stackable=True,
    )
    _promotion(
        client, admin["headers"], sku["id"],
        name="满 150 减 20", promotion_type="threshold_discount",
        threshold_amount="150.00", discount_amount="20.00", stackable=True,
    )
    _promotion(client, admin["headers"], sku["id"], name="券 10", stackable=True)

    data = client.post(
        "/api/commerce/price-preview",
        json={"sku_id": sku["id"], "quantity": 2, "at_time": "2026-11-11T20:00:00"},
        headers=admin["headers"],
    ).json()["data"]
    assert data["final_total"] == "168.00"
    assert [p["promotion_type"] for p in data["applied_promotions"]] == [
        "live_price", "threshold_discount", "coupon",
    ]


def test_promotion_validates_rule_shape_and_permission(client, make_user):
    admin = make_user("admin")
    employee = make_user("employee")
    _, sku = _create_product_and_sku(client, admin)

    forbidden = client.post(
        "/api/commerce/promotions",
        json={
            "name": "越权活动", "promotion_type": "live_price", "sku_ids": [sku["id"]],
            "fixed_price": "99.00", "starts_at": "2026-10-01T00:00:00",
            "ends_at": "2026-12-31T00:00:00",
        },
        headers=employee["headers"],
    ).json()
    assert forbidden["code"] == ErrorCode.FORBIDDEN

    missing_price = client.post(
        "/api/commerce/promotions",
        json={
            "name": "缺价格", "promotion_type": "live_price", "sku_ids": [sku["id"]],
            "starts_at": "2026-10-01T00:00:00", "ends_at": "2026-12-31T00:00:00",
        },
        headers=admin["headers"],
    ).json()
    assert missing_price["code"] == ErrorCode.PARAM_ERROR


def _create_session_with_item(client, owner, sku, **item_overrides):
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "巡检演示场", "starts_at": "2026-11-11T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]
    item = {
        "sku_id": sku["id"],
        "planned_sales": 100,
        "planned_unit_price": "119.00",
        "sort_order": 1,
    }
    item.update(item_overrides)
    result = client.post(
        f"/api/commerce/sessions/{session['id']}/items",
        json=item,
        headers=owner["headers"],
    ).json()
    assert result["code"] == 0, result
    return session


def test_inspection_blocks_empty_session_and_updates_session_status(client, make_user):
    owner = make_user("employee")
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "空场次", "starts_at": "2026-11-11T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]

    result = client.post(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=owner["headers"],
    ).json()
    assert result["code"] == 0, result
    batch = result["data"]
    assert batch["conclusion"] == "blocked"
    assert batch["blocker_count"] == 1
    assert batch["results"][0]["rule_code"] == "SESSION_HAS_ITEMS"
    assert batch["results"][0]["status"] == "failed"

    detail = client.get(
        f"/api/commerce/sessions/{session['id']}", headers=owner["headers"]
    ).json()["data"]
    assert detail["status"] == "needs_confirmation"


def test_inspection_reports_inventory_price_and_document_risks(client, make_user):
    admin = make_user("admin")
    owner = make_user("employee")
    _, sku = _create_product_and_sku(client, admin)
    session = _create_session_with_item(
        client, owner, sku, planned_unit_price=None
    )

    batch = client.post(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=owner["headers"],
    ).json()["data"]
    failed_codes = {
        row["rule_code"] for row in batch["results"] if row["status"] == "failed"
    }
    assert batch["conclusion"] == "blocked"
    assert batch["blocker_count"] == 2
    assert batch["warning_count"] == 3
    assert {
        "INVENTORY_SUFFICIENT",
        "PROMOTION_ACTIVE",
        "PRICE_MATCH",
        "PRODUCT_MANUAL_VALID",
        "CLAIM_EVIDENCE_VALID",
    } <= failed_codes


def test_inspection_marks_fully_prepared_session_ready(
    client, make_user, session_factory
):
    admin = make_user("admin")
    owner = make_user("employee")
    product, sku = _create_product_and_sku(client, admin)
    session = _create_session_with_item(client, owner, sku)

    inventory = client.post(
        "/api/commerce/inventory",
        json={
            "sku_id": sku["id"],
            "available_quantity": 1000,
            "reserved_quantity": 100,
            "source": "mock-platform",
        },
        headers=admin["headers"],
    ).json()
    assert inventory["code"] == 0, inventory
    _promotion(
        client,
        admin["headers"],
        sku["id"],
        name="直播券 10",
        discount_amount="10.00",
        stackable=True,
    )

    with session_factory() as db:
        kb = KnowledgeBase(
            name="直播商品资料",
            description="巡检测试资料",
            owner_id=admin["id"],
            is_public=True,
        )
        db.add(kb)
        db.flush()
        for index, document_type in enumerate(
            (DOC_TYPE_PRODUCT_MANUAL, DOC_TYPE_CLAIM_EVIDENCE), start=1
        ):
            db.add(Document(
                knowledge_base_id=kb.id,
                uploader_id=admin["id"],
                product_id=product["id"],
                sku_id=sku["id"],
                document_type=document_type,
                version="v1",
                filename=f"资料-{index}.txt",
                stored_name=f"inspection-{index}.txt",
                file_path=f"/tmp/inspection-{index}.txt",
                file_type="txt",
                file_size=10,
                status=STATUS_COMPLETED,
                chunk_count=1,
                char_count=10,
            ))
        db.commit()

    response = client.post(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=owner["headers"],
    ).json()
    assert response["code"] == 0, response
    batch = response["data"]
    assert batch["conclusion"] == "ready"
    assert batch["score"] == 100
    assert batch["blocker_count"] == 0
    assert batch["warning_count"] == 0
    assert batch["passed_count"] == 8

    history = client.get(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=owner["headers"],
    ).json()["data"]
    assert history[0]["id"] == batch["id"]
    assert len(history[0]["results"]) == 8

    session_detail = client.get(
        f"/api/commerce/sessions/{session['id']}", headers=owner["headers"]
    ).json()["data"]
    assert session_detail["status"] == "ready"


def test_inspection_respects_session_ownership(client, make_user):
    owner = make_user("employee")
    stranger = make_user("employee")
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "私有巡检场", "starts_at": "2026-11-11T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]

    result = client.post(
        f"/api/commerce/sessions/{session['id']}/inspections",
        headers=stranger["headers"],
    ).json()
    assert result["code"] == ErrorCode.NOT_FOUND

