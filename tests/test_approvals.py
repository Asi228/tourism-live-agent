"""高风险操作审批：申请 → 审批 → 生成唯一执行任务。

盯四件事（都是真实系统里会出事的地方）：
  1. **只有本场选品**能申请修复（不能改一场没选的商品）；
  2. 预期值在入口就规整（99 / 99.0 / "99.00" 落库都是 99.00）—— 审批看到的数就是执行的数；
  3. **申请人不能批自己**（四眼原则）；非场次负责人不能批；
  4. 通过 = 生成**唯一**任务，重复审批被拒；驳回不生成任务。
"""

from datetime import datetime, timedelta

from app.core.response import ErrorCode
from app.models.rpa import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TASK_PRICE_FIX,
)


def _seed_session(client, admin, owner, *, with_sku=True):
    """admin 建商品目录（需管理员），owner 建一场直播并选 SKU；返回 (session_id, sku_id)。"""
    product = client.post(
        "/api/commerce/products",
        json={"name": "山茶花修护洗发水", "brand": "云岚", "category": "洗护"},
        headers=admin["headers"],
    ).json()["data"]
    sku = client.post(
        f"/api/commerce/products/{product['id']}/skus",
        json={"sku_code": "YL-SH-450", "specification": "450ml", "list_price": "129.00"},
        headers=admin["headers"],
    ).json()["data"]
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": "双十一洗护专场",
            "starts_at": (datetime.now() + timedelta(days=3)).isoformat(),
        },
        headers=owner["headers"],
    ).json()["data"]
    if with_sku:
        r = client.post(
            f"/api/commerce/sessions/{session['id']}/items",
            json={"sku_id": sku["id"], "planned_sales": 800, "sort_order": 1},
            headers=owner["headers"],
        ).json()
        assert r["code"] == 0, r
    return session["id"], sku["id"]


def _apply(client, user, session_id, sku_id, *, live_price="99", reason="巡检发现价格不一致"):
    return client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session_id,
            "task_type": TASK_PRICE_FIX,
            "sku_id": sku_id,
            "expected_value": {"live_price": live_price},
            "reason": reason,
        },
        headers=user["headers"],
    ).json()


def test_apply_normalizes_price_and_blocks_duplicates(client, make_user):
    """预期值规整成两位小数；同对象同类型 pending 时不允许再申请。"""
    admin = make_user("admin")
    emp = make_user()
    sid, sku_id = _seed_session(client, admin, emp)

    body = _apply(client, emp, sid, sku_id, live_price="99")
    assert body["code"] == 0, body
    assert body["data"]["expected_value"] == {"live_price": "99.00"}
    assert body["data"]["status"] == APPROVAL_PENDING

    dup = _apply(client, emp, sid, sku_id, live_price="89")
    assert dup["code"] == ErrorCode.PARAM_ERROR, "重复申请应被拒绝"


def test_apply_requires_sku_in_session(client, make_user):
    """SKU 不在本场选品里 → 拒绝（不能改一场没选的商品）。"""
    admin = make_user("admin")
    emp = make_user()
    sid, _ = _seed_session(client, admin, emp, with_sku=False)
    # 另建一个 SKU 但不加入选品
    product = client.post(
        "/api/commerce/products",
        json={"name": "护发素", "brand": "云岚", "category": "洗护"},
        headers=admin["headers"],
    ).json()["data"]
    other = client.post(
        f"/api/commerce/products/{product['id']}/skus",
        json={"sku_code": "YL-HS-300", "specification": "300ml", "list_price": "59.00"},
        headers=admin["headers"],
    ).json()["data"]

    body = _apply(client, emp, sid, other["id"])
    assert body["code"] == ErrorCode.PARAM_ERROR
    assert "不在本场选品" in body["message"]


def test_requester_cannot_approve_own_request(client, make_user):
    """申请人不能批自己的单 —— 四眼原则。"""
    admin = make_user("admin")
    emp = make_user()
    sid, sku_id = _seed_session(client, admin, emp)
    approval_id = _apply(client, emp, sid, sku_id)["data"]["id"]

    body = client.post(
        f"/api/rpa/approvals/{approval_id}/decision",
        json={"approve": True},
        headers=emp["headers"],
    ).json()
    assert body["code"] == ErrorCode.FORBIDDEN


def test_admin_approves_and_task_is_created_once(client, make_user):
    """管理员审批通过 → 生成唯一任务；重复审批被拒（幂等键复用审批单）。"""
    emp = make_user()
    admin = make_user("admin")
    emp = make_user()
    sid, sku_id = _seed_session(client, admin, emp)
    approval_id = _apply(client, emp, sid, sku_id)["data"]["id"]

    body = client.post(
        f"/api/rpa/approvals/{approval_id}/decision",
        json={"approve": True, "note": "确认调价"},
        headers=admin["headers"],
    ).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["status"] == APPROVAL_APPROVED
    assert data["task_id"], "通过后应生成执行任务"
    assert data["approved_by"] == admin["id"]

    # 任务幂等键 = 审批单的键（唯一约束保证一张单最多执行一次）
    tasks = client.get("/api/rpa/tasks", headers=admin["headers"]).json()["data"]
    task = next(t for t in tasks if t["id"] == data["task_id"])
    assert task["task_type"] == TASK_PRICE_FIX
    assert task["payload"]["approval_id"] == approval_id
    assert task["payload"]["expected"] == {"live_price": "99.00"}

    again = client.post(
        f"/api/rpa/approvals/{approval_id}/decision",
        json={"approve": True},
        headers=admin["headers"],
    ).json()
    assert again["code"] == ErrorCode.PARAM_ERROR, "重复审批应被拒绝"


def test_reject_creates_no_task(client, make_user):
    """驳回：状态 rejected，不生成任务。"""
    admin = make_user("admin")
    emp = make_user()
    sid, sku_id = _seed_session(client, admin, emp)
    approval_id = _apply(client, emp, sid, sku_id)["data"]["id"]
    before = len(client.get("/api/rpa/tasks", headers=admin["headers"]).json()["data"])

    body = client.post(
        f"/api/rpa/approvals/{approval_id}/decision",
        json={"approve": False, "note": "活动已结束，不必调价"},
        headers=admin["headers"],
    ).json()
    assert body["code"] == 0, body
    assert body["data"]["status"] == APPROVAL_REJECTED
    assert body["data"]["task_id"] is None
    after = len(client.get("/api/rpa/tasks", headers=admin["headers"]).json()["data"])
    assert after == before, "驳回不该生成任务"


def test_delist_expected_must_be_inactive(client, make_user):
    """下架申请的预期状态只能是 inactive —— 别让"下架"混进别的状态。"""
    admin = make_user("admin")
    emp = make_user()
    sid, sku_id = _seed_session(client, admin, emp)
    body = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": sid,
            "task_type": "delist",
            "sku_id": sku_id,
            "expected_value": {"platform_status": "active"},
        },
        headers=emp["headers"],
    ).json()
    assert body["code"] == ErrorCode.PARAM_ERROR


def test_non_owner_cannot_apply(client, make_user):
    """非本场负责人（普通员工）不能发起申请 —— 场次可见性即权限。"""
    admin = make_user("admin")
    owner = make_user()
    other = make_user()
    sid, sku_id = _seed_session(client, admin, owner)
    body = _apply(client, other, sid, sku_id)
    assert body["code"] == ErrorCode.NOT_FOUND, "看不到的场次按不存在处理"
