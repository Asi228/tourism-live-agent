"""公网互动沙箱：只放行业务闭环，并让轻量 RPA 留下完整执行证据。"""

from datetime import datetime, timedelta

import pytest

from app import _demo_sandbox_write_allowed
from app.config import settings


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/commerce/sessions"),
        ("POST", "/api/commerce/sessions/12/items"),
        ("POST", "/api/commerce/sessions/12/inspections"),
        ("POST", "/api/rpa/tasks"),
        ("POST", "/api/rpa/approvals/7/decision"),
        ("POST", "/api/agent/live-sessions/3/explain"),
        ("DELETE", "/api/conversations/9"),
    ],
)
def test_demo_sandbox_allows_operational_workflow(method, path):
    assert _demo_sandbox_write_allowed(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/users"),
        ("PATCH", "/api/users/1/role"),
        ("DELETE", "/api/knowledge/1"),
        ("POST", "/api/commerce/products"),
        ("PUT", "/api/rpa/mock-platform/skus/1"),
    ],
)
def test_demo_sandbox_protects_accounts_and_base_data(method, path):
    assert not _demo_sandbox_write_allowed(method, path)


def test_demo_collection_task_completes_without_external_worker(
    client, make_user, monkeypatch
):
    admin = make_user("admin")
    operator = make_user("employee")
    product = client.post(
        "/api/commerce/products",
        json={"name": "演示门票", "brand": "测试景区", "category": "景区门票"},
        headers=admin["headers"],
    ).json()["data"]
    sku = client.post(
        f"/api/commerce/products/{product['id']}/skus",
        json={"sku_code": "DEMO-SANDBOX-001", "specification": "成人票", "list_price": "99.00"},
        headers=admin["headers"],
    ).json()["data"]
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": "互动沙箱专场",
            "starts_at": (datetime.now() + timedelta(days=1)).isoformat(),
        },
        headers=operator["headers"],
    ).json()["data"]
    client.post(
        f"/api/commerce/sessions/{session['id']}/items",
        json={"sku_id": sku["id"], "planned_sales": 20, "planned_unit_price": "79.00"},
        headers=operator["headers"],
    )

    monkeypatch.setattr(settings, "DEMO_READ_ONLY", True)
    monkeypatch.setattr(settings, "DEMO_READ_ONLY_USERNAME", operator["username"])
    result = client.post(
        "/api/rpa/tasks",
        json={"session_id": session["id"]},
        headers=operator["headers"],
    ).json()

    assert result["code"] == 0
    assert result["data"]["status"] == "succeeded"
    assert result["data"]["result"]["verified"] is True
    assert result["data"]["result"]["inspection"] == {
        "status": "completed",
        "conclusion": "ready",
        "score": 100,
        "blocker_count": 0,
        "warning_count": 0,
    }
    assert len(result["data"]["result"]["snapshots"]) == 1
    assert [step["step_code"] for step in result["data"]["steps"]] == [
        "login", "collect", "persist", "inspect"
    ]


def test_session_owner_can_see_and_execute_another_users_demo_approval(
    client, make_user, monkeypatch
):
    admin = make_user("admin")
    owner = make_user("employee")
    product = client.post(
        "/api/commerce/products",
        json={"name": "审批演示酒店", "brand": "测试酒店", "category": "酒店套餐"},
        headers=admin["headers"],
    ).json()["data"]
    sku = client.post(
        f"/api/commerce/products/{product['id']}/skus",
        json={"sku_code": "DEMO-APPROVAL-001", "specification": "一晚", "list_price": "599.00"},
        headers=admin["headers"],
    ).json()["data"]
    session = client.post(
        "/api/commerce/sessions",
        json={"title": "审批互动专场", "starts_at": "2026-12-01T20:00:00"},
        headers=owner["headers"],
    ).json()["data"]
    client.post(
        f"/api/commerce/sessions/{session['id']}/items",
        json={"sku_id": sku["id"], "planned_sales": 10, "planned_unit_price": "499.00"},
        headers=owner["headers"],
    )
    approval = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session["id"],
            "task_type": "price_fix",
            "sku_id": sku["id"],
            "expected_value": {"live_price": "499.00"},
            "reason": "申请同步直播价格",
        },
        headers=admin["headers"],
    ).json()["data"]

    visible_ids = {
        row["id"] for row in client.get("/api/rpa/approvals", headers=owner["headers"]).json()["data"]
    }
    assert approval["id"] in visible_ids

    monkeypatch.setattr(settings, "DEMO_READ_ONLY", True)
    monkeypatch.setattr(settings, "DEMO_READ_ONLY_USERNAME", owner["username"])
    decided = client.post(
        f"/api/rpa/approvals/{approval['id']}/decision",
        json={"approve": True, "note": "规则与供应商确认单一致"},
        headers=owner["headers"],
    ).json()
    assert decided["code"] == 0
    assert decided["data"]["status"] == "executed"
    task = client.get(
        f"/api/rpa/tasks/{decided['data']['task_id']}", headers=owner["headers"]
    ).json()["data"]
    assert task["status"] == "succeeded"
    assert task["result"]["verified"] is True
    assert task["result"]["expected"] == {"live_price": "499.00"}
    assert task["result"]["approval_id"] == approval["id"]
    assert len(task["steps"]) == 4
