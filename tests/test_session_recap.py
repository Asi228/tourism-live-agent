"""单场直播复盘：业务口径、耗时统计和场次权限。"""

from datetime import datetime, timedelta

from app.models.rpa import TASK_FAILED, TASK_SUCCEEDED

from tests.test_rpa_metrics import _seed_owned_session, _task


def test_session_recap_aggregates_real_business_records(
    client, make_user, session_factory
):
    admin = make_user("admin")
    owner = make_user("employee")
    outsider = make_user("employee")
    session_id, sku_id = _seed_owned_session(
        client, admin, owner, suffix="RECAP"
    )

    inspection = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=owner["headers"],
    ).json()
    assert inspection["code"] == 0

    approval = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session_id,
            "task_type": "price_fix",
            "sku_id": sku_id,
            "expected_value": {"live_price": "99.00"},
            "reason": "复盘测试申请",
        },
        headers=owner["headers"],
    ).json()
    assert approval["code"] == 0, approval

    now = datetime.now()
    with session_factory() as db:
        _task(
            db, session_id=session_id, user_id=owner["id"],
            status=TASK_SUCCEEDED, started=now - timedelta(seconds=10),
            completed=now,
        )
        _task(
            db, session_id=session_id, user_id=owner["id"],
            status=TASK_FAILED, started=now - timedelta(seconds=30),
            completed=now, attempts=2,
        )
        db.commit()

    body = client.get(
        f"/api/commerce/sessions/{session_id}/recap",
        headers=owner["headers"],
    ).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["session_id"] == session_id
    assert data["readiness"] == "blocked"
    assert "阻断项" in data["headline"]
    assert data["sku_count"] == 1
    assert data["planned_sales"] == 20
    assert data["planned_gmv"] == "1980.00"
    assert data["items"][0] == {
        "sku_id": sku_id,
        "sku_code": "METRIC-RECAP",
        "product_name": "监控商品RECAP",
        "specification": "标准装",
        "planned_sales": 20,
        "planned_unit_price": "99.00",
        "planned_gmv": "1980.00",
    }
    assert data["inspection"]["total_runs"] == 1
    assert data["inspection"]["latest"]["conclusion"] == "blocked"
    assert data["inspection"]["unresolved_risks"]
    assert data["automation"] == {
        **data["automation"],
        "total": 2,
        "queued": 0,
        "running": 0,
        "succeeded": 1,
        "failed": 1,
        "success_rate": 50.0,
        "avg_duration_seconds": 20.0,
        "retry_task_count": 1,
    }
    assert len(data["automation"]["tasks"]) == 2
    assert data["approvals"]["total"] == 1
    assert data["approvals"]["pending"] == 1
    assert data["approvals"]["approvals"][0]["sku_code"] == "METRIC-RECAP"

    denied = client.get(
        f"/api/commerce/sessions/{session_id}/recap",
        headers=outsider["headers"],
    ).json()
    assert denied["code"] == 40400

    admin_body = client.get(
        f"/api/commerce/sessions/{session_id}/recap",
        headers=admin["headers"],
    ).json()
    assert admin_body["code"] == 0


def test_session_recap_is_explicit_when_no_inspection(client, make_user):
    user = make_user("employee")
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": "尚未巡检场次",
            "starts_at": (datetime.now() + timedelta(days=1)).isoformat(),
        },
        headers=user["headers"],
    ).json()["data"]

    data = client.get(
        f"/api/commerce/sessions/{session['id']}/recap",
        headers=user["headers"],
    ).json()["data"]
    assert data["readiness"] == "not_inspected"
    assert "尚未完成巡检" in data["headline"]
    assert data["inspection"] == {
        "total_runs": 0, "latest": None, "unresolved_risks": [],
    }
    assert data["automation"]["success_rate"] is None
    assert data["approvals"]["avg_decision_seconds"] is None
