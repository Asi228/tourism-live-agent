"""运营监控指标：口径、异常明细和用户数据隔离。"""

from datetime import datetime, timedelta
from uuid import uuid4

from app.models.rpa import TASK_FAILED, TASK_QUEUED, TASK_SUCCEEDED
from app.repositories import rpa_repository as rpa_repo


def _seed_owned_session(client, admin, owner, *, suffix: str):
    product = client.post(
        "/api/commerce/products",
        json={"name": f"监控商品{suffix}", "brand": "云岚", "category": "洗护"},
        headers=admin["headers"],
    ).json()["data"]
    sku = client.post(
        f"/api/commerce/products/{product['id']}/skus",
        json={
            "sku_code": f"METRIC-{suffix}",
            "specification": "标准装",
            "list_price": "129.00",
        },
        headers=admin["headers"],
    ).json()["data"]
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": f"监控专场{suffix}",
            "starts_at": (datetime.now() + timedelta(days=2)).isoformat(),
        },
        headers=owner["headers"],
    ).json()["data"]
    client.post(
        f"/api/commerce/sessions/{session['id']}/items",
        json={
            "sku_id": sku["id"],
            "planned_sales": 20,
            "planned_unit_price": "99.00",
            "sort_order": 1,
        },
        headers=owner["headers"],
    )
    return session["id"], sku["id"]


def _task(db, *, session_id, user_id, status, started=None, completed=None, attempts=1):
    return rpa_repo.create_task(
        db,
        session_id=session_id,
        task_type="collect_platform_data",
        status=status,
        idempotency_key=uuid4().hex,
        requested_by=user_id,
        payload={"session_id": session_id},
        error_message="平台登录失败" if status == TASK_FAILED else None,
        attempt_count=attempts,
        started_at=started,
        completed_at=completed,
        created_at=datetime.now() - timedelta(hours=1),
    )


def test_operational_metrics_counts_duration_alerts_and_scope(
    client, make_user, session_factory
):
    admin = make_user("admin")
    owner = make_user("employee")
    outsider = make_user("employee")
    session_id, sku_id = _seed_owned_session(client, admin, owner, suffix="OWNER")
    other_session_id, _ = _seed_owned_session(client, admin, outsider, suffix="OTHER")

    # 缺库存、价格、资料会形成当前阻断场次。
    inspection = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=owner["headers"],
    ).json()
    assert inspection["data"]["conclusion"] == "blocked"

    approval = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session_id,
            "task_type": "price_fix",
            "sku_id": sku_id,
            "expected_value": {"live_price": "99.00"},
            "reason": "监控指标测试",
        },
        headers=owner["headers"],
    ).json()
    assert approval["code"] == 0, approval

    now = datetime.now()
    with session_factory() as db:
        _task(
            db, session_id=session_id, user_id=owner["id"], status=TASK_SUCCEEDED,
            started=now - timedelta(seconds=10), completed=now,
        )
        _task(
            db, session_id=session_id, user_id=owner["id"], status=TASK_FAILED,
            started=now - timedelta(seconds=30), completed=now, attempts=2,
        )
        _task(
            db, session_id=session_id, user_id=owner["id"], status=TASK_FAILED,
            started=now - timedelta(seconds=40), completed=now, attempts=2,
        )
        _task(db, session_id=session_id, user_id=owner["id"], status=TASK_QUEUED)
        _task(
            db, session_id=other_session_id, user_id=outsider["id"], status=TASK_SUCCEEDED,
            started=now - timedelta(seconds=5), completed=now,
        )
        db.commit()

    body = client.get("/api/rpa/metrics?days=30", headers=owner["headers"]).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["tasks"] == {
        "total": 4,
        "queued": 1,
        "running": 0,
        "succeeded": 1,
        "failed": 2,
        "terminal": 3,
        "success_rate": 33.33,
        "avg_duration_seconds": 26.67,
        "retry_task_count": 2,
    }
    assert data["approvals"]["pending"] == 1
    assert data["sessions"] == {
        "total": 1, "ready": 0, "blocked": 1, "without_inspection": 0,
    }
    assert {row["alert_type"] for row in data["alerts"]} == {
        "task_failed", "approval_pending", "session_blocked",
    }
    assert all(row["session_id"] == session_id for row in data["alerts"])
    failed_alert = next(
        row for row in data["alerts"] if row["alert_type"] == "task_failed"
    )
    assert failed_alert["occurrence_count"] == 2
    assert "相同异常已合并 2 次" in failed_alert["detail"]

    admin_data = client.get("/api/rpa/metrics?days=30", headers=admin["headers"]).json()["data"]
    assert admin_data["tasks"]["total"] == 5
    assert admin_data["sessions"]["total"] == 2


def test_operational_metrics_rejects_invalid_window(client, make_user):
    user = make_user("admin")
    body = client.get("/api/rpa/metrics?days=0", headers=user["headers"]).json()
    assert body["code"] == 40000
    assert "1 到 365" in body["message"]
