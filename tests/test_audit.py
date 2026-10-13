"""审计接口（`GET /audit`）与"权限变更必须留痕"的测试。

两件事放一起，是因为它们是同一个契约的两面：**能改变"谁能看到什么"的动作都要留痕**，
而 `/audit` 是读这些痕迹的入口。
"""

import pytest
from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import AuditLog


# ==================== 审计接口 ====================


def test_audit_endpoint_is_admin_only(client, make_user):
    emp = make_user("employee")
    r = client.get("/api/audit", headers=emp["headers"])
    assert r.json()["code"] == ErrorCode.FORBIDDEN


def test_audit_endpoint_returns_action_text(client, make_user):
    """审计列表带中文说明 —— 前端不用维护一份 action → 文案的映射。"""
    admin = make_user("admin")
    emp = make_user("employee")
    client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "admin"},
        headers=admin["headers"],
    )

    r = client.get("/api/audit", headers=admin["headers"])
    assert r.json()["code"] == 0
    data = r.json()["data"]
    assert data["has_more"] is False
    rows = data["items"]
    assert rows[0]["action"] == "user.role_change"
    assert rows[0]["action_text"] == "变更角色"


def test_audit_can_filter_by_action_and_actor(client, make_user):
    """`action` / `actor_id` 两个筛选要真的生效（表上的两个索引就是为它们建的）。"""
    admin = make_user("admin")
    emp = make_user("employee")

    client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "admin"},
        headers=admin["headers"],
    )
    client.post("/api/knowledge", json={"name": "用于筛选的库"}, headers=admin["headers"])

    r = client.get("/api/audit", params={"action": "user.role_change"}, headers=admin["headers"])
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["action"] == "user.role_change"

    r = client.get("/api/audit", params={"actor_id": admin["id"]}, headers=admin["headers"])
    assert len(r.json()["data"]["items"]) == 2, "两条都是这个管理员做的"

    # 不存在的 actor：空列表而不是报错
    r = client.get("/api/audit", params={"actor_id": 99999}, headers=admin["headers"])
    assert r.json()["data"]["items"] == []


def test_audit_keyset_paging(client, make_user):
    """keyset 翻页：before_id 传当前最旧那条的 id，翻到的是更早的，不重不漏。"""
    admin = make_user("admin")
    for i in range(3):
        client.post("/api/knowledge", json={"name": f"翻页库{i}"}, headers=admin["headers"])

    page1 = client.get("/api/audit", params={"limit": 2}, headers=admin["headers"]).json()["data"]
    assert len(page1["items"]) == 2 and page1["has_more"] is True

    oldest = page1["items"][-1]["id"]
    page2 = client.get(
        "/api/audit", params={"limit": 2, "before_id": oldest}, headers=admin["headers"]
    ).json()["data"]
    assert page2["has_more"] is False
    assert len(page2["items"]) == 1
    assert page2["items"][0]["id"] < oldest, "翻到的是更早的，不是重复的"


def test_audit_actions_endpoint(client, make_user):
    """动作清单由后端下发 —— 前端不该硬编码第二份。"""
    admin = make_user("admin")
    r = client.get("/api/audit/actions", headers=admin["headers"])
    assert r.json()["code"] == 0
    actions = {a["action"] for a in r.json()["data"]}
    assert "user.role_change" in actions
    assert "kb.visibility_change" in actions


# ==================== 可见性变更必须留痕 ====================


def test_public_visibility_change_writes_audit(client, make_user, session_factory):
    """🔴 `is_public` 是**权限开关**不是元数据 —— 改它必须留痕。

    置 True 之后所有登录用户至少 read，而且**下一次请求就生效**
    （`get_accessible_kb_ids` 立刻把它算进检索范围）——
    比改角色生效还快，无痕的话就是"谁能看到什么"被静默改变。
    """
    admin = make_user("admin")
    kb = client.post(
        "/api/knowledge", json={"name": "财务库", "is_public": False}, headers=admin["headers"]
    ).json()["data"]

    r = client.put(
        f"/api/knowledge/{kb['id']}", json={"is_public": True}, headers=admin["headers"]
    )
    assert r.json()["code"] == 0, r.json()

    with session_factory() as db:
        rows = (
            db.execute(
                select(AuditLog).where(AuditLog.action == "kb.visibility_change")
            ).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].detail == {"from": "private", "to": "public"}
    assert rows[0].target_name == "财务库"


def test_renaming_does_not_write_visibility_audit(client, make_user, session_factory):
    """改名字不写可见性审计 —— 边界：只记"谁能看到什么"，不记元数据。

    （改名字本身影响的是"叫什么"，不改变可见范围。这条用例把边界钉住，
    顺便防止将来有人图省事把 update_kb 整个接进审计。）
    """
    admin = make_user("admin")
    kb = client.post(
        "/api/knowledge", json={"name": "旧名字"}, headers=admin["headers"]
    ).json()["data"]

    client.put(f"/api/knowledge/{kb['id']}", json={"name": "新名字"}, headers=admin["headers"])

    with session_factory() as db:
        rows = (
            db.execute(
                select(AuditLog).where(AuditLog.action == "kb.visibility_change")
            ).scalars().all()
        )
    assert rows == []


def test_same_visibility_value_writes_nothing(client, make_user, session_factory):
    """把 public 再设成 public 不该产生审计（否则一次表单提交会刷一条噪声）。"""
    admin = make_user("admin")
    kb = client.post(
        "/api/knowledge", json={"name": "公开库", "is_public": True}, headers=admin["headers"]
    ).json()["data"]

    client.put(
        f"/api/knowledge/{kb['id']}", json={"is_public": True}, headers=admin["headers"]
    )

    with session_factory() as db:
        rows = (
            db.execute(
                select(AuditLog).where(AuditLog.action == "kb.visibility_change")
            ).scalars().all()
        )
    assert rows == []


