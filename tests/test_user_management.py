"""用户管理 / 概览统计 / 审计 的测试。

重点在**改角色这条提权入口的三条约束**（不能改自己、不能让 admin 归零、必须留审计）——
它们不是"顺手加的校验"，而是"这个接口能不能开"的前提。
"""

import pytest
from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import AuditLog


def _users(client, admin, **params):
    return client.get("/api/users", headers=admin["headers"], params=params).json()


# ==================== 改角色：三条约束 ====================


def test_employee_cannot_change_role(client, make_user):
    """普通员工调改角色 → 40300（require_admin 挡住）。"""
    emp = make_user("employee")
    other = make_user("employee")

    r = client.patch(
        f"/api/users/{other['id']}/role",
        json={"role_code": "admin"},
        headers=emp["headers"],
    )
    assert r.json()["code"] == ErrorCode.FORBIDDEN, r.json()


def test_admin_cannot_change_own_role(client, make_user):
    """🔴 不能改自己 —— 防手滑自锁（唯一的 admin 把自己降级后就没人能改回来了）。"""
    admin = make_user("admin")

    r = client.patch(
        f"/api/users/{admin['id']}/role",
        json={"role_code": "employee"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR
    assert "自己" in r.json()["message"]


def test_cannot_demote_the_last_admin(session_factory, make_user):
    """🔴 不能让 admin 归零。

    ⚠️ **这条约束在 HTTP 层永远触发不到**，说明一下为什么 —— 值得写下来，
    否则将来有人看到"跑不到的代码"会以为它是死代码删掉：

      · 要降级 A（admin），操作者必须是 admin B（`require_admin`）；
      · 那么系统里至少有 A 和 B 两个 admin → `count_admins()` = 2 > 1，闸门不生效；
      · 而 A 降级自己会被"不能改自己"提前挡掉。

    也就是说它是**纵深防御**：挡的不是当前的接口，而是"将来某个直接调 service 的
    场景"（后台脚本、批量运维、或者哪天有人给这个函数加了一个新的调用方而忘了
    检查权限）。所以这里**绕过接口层直接调 service** 来验证闸门本身有效。
    """
    import pytest

    from app.core.exceptions import AppException
    from app.models.user import User
    from app.services import user_service

    only_admin = make_user("admin")            # 系统里唯一的 admin
    non_admin_actor = make_user("employee")    # 以非 admin 身份直接调 service

    with session_factory() as db:
        actor = db.get(User, non_admin_actor["id"])
        assert user_service.count_admins(db) == 1
        with pytest.raises(AppException) as ei:
            user_service.change_role(
                db, actor=actor, target_id=only_admin["id"], role_code="employee"
            )
        assert "至少要保留一个管理员" in ei.value.message


def test_role_code_must_exist(client, make_user):
    """角色 code 不存在 → 40000 而不是 40400：这是"入参不合法"，不是"对象不存在"。"""
    admin = make_user("admin")
    emp = make_user("employee")

    r = client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "nonexistent"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR
    assert "角色不存在" in r.json()["message"]


def test_change_role_writes_audit(client, make_user, session_factory):
    """🔴 改角色必须留痕：谁、把谁、从什么改成什么。"""
    admin = make_user("admin")
    emp = make_user("employee")

    r = client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "admin"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["changed"] is True

    with session_factory() as db:
        rows = db.execute(select(AuditLog)).scalars().all()
    assert len(rows) == 1, "一次改角色应当恰好留一条审计"
    row = rows[0]
    assert row.action == "user.role_change"
    assert row.actor_id == admin["id"]
    assert row.actor_name == admin["username"]
    assert row.target_id == emp["id"]
    assert row.detail == {"from": "employee", "to": "admin"}


def test_same_role_is_idempotent(client, make_user):
    """重复提交同一个角色 → 成功但 changed=false（不当成错误，前端据此提示"未变化"）。"""
    admin = make_user("admin")
    emp = make_user("employee")

    r = client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "employee"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0
    assert r.json()["data"]["changed"] is False


def test_role_change_is_reflected_in_list(client, make_user):
    """改完再看列表，角色确实是新的（不是只改了返回值）。"""
    admin = make_user("admin")
    emp = make_user("employee")

    client.patch(
        f"/api/users/{emp['id']}/role",
        json={"role_code": "admin"},
        headers=admin["headers"],
    )
    body = _users(client, admin, keyword=emp["username"], full=True)
    item = next(u for u in body["data"] if u["id"] == emp["id"])
    assert item["role"]["code"] == "admin"


# ==================== 用户列表的两种结构 ====================


def test_user_list_full_contains_email_but_never_password(client, make_user):
    """`full=true` 给邮箱与注册时间 —— 但**任何情况下**都不能有 password_hash。"""
    admin = make_user("admin")
    emp = make_user("employee")

    body = _users(client, admin, keyword=emp["username"], full=True)
    item = next(u for u in body["data"] if u["id"] == emp["id"])
    assert "email" in item and "created_at" in item
    assert "password" not in str(item).lower()


def test_user_list_default_stays_minimal(client, make_user):
    """默认仍是精简结构 —— 这条守卫是给"选人授权"下拉用的，不该为管理页放宽。"""
    admin = make_user("admin")
    emp = make_user("employee")

    body = _users(client, admin, keyword=emp["username"])
    item = next(u for u in body["data"] if u["id"] == emp["id"])
    assert set(item.keys()) == {"id", "username", "is_active", "role"}


# ==================== 授权情况 ====================


def test_grants_lists_kb_permissions(client, make_user):
    """从"人"这一侧看权限：他被授权了哪些库。"""
    admin = make_user("admin")
    emp = make_user("employee")

    kb = client.post(
        "/api/knowledge", json={"name": "被授权的库"}, headers=admin["headers"]
    ).json()["data"]
    client.post(
        f"/api/knowledge/{kb['id']}/members",
        json={"user_id": emp["id"], "permission": "write"},
        headers=admin["headers"],
    )

    r = client.get(f"/api/users/{emp['id']}/grants", headers=admin["headers"])
    assert r.json()["code"] == 0
    rows = r.json()["data"]
    assert len(rows) == 1
    assert rows[0]["knowledge_base_name"] == "被授权的库"
    assert rows[0]["permission"] == "write"


def test_grant_writes_audit(client, make_user, session_factory):
    """授权也要留痕（含"之前的权限"—— 新增授权与提权是两件事）。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb = client.post(
        "/api/knowledge", json={"name": "审计库"}, headers=admin["headers"]
    ).json()["data"]

    client.post(
        f"/api/knowledge/{kb['id']}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )
    client.post(
        f"/api/knowledge/{kb['id']}/members",
        json={"user_id": emp["id"], "permission": "admin"},
        headers=admin["headers"],
    )

    with session_factory() as db:
        rows = (
            db.execute(
                select(AuditLog).where(AuditLog.action == "kb.member_grant")
                .order_by(AuditLog.id)
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert rows[0].detail["previous"] is None, "首次授权 previous 应为空"
    assert rows[1].detail["previous"] == "read", "第二次是提权，要记下原权限"


# ==================== 概览统计 ====================


def test_overview_returns_expected_shape(client, make_user):
    admin = make_user("admin")
    r = client.get("/api/stats/overview", headers=admin["headers"])
    assert r.json()["code"] == 0
    d = r.json()["data"]
    assert set(d.keys()) == {
        "scope", "documents", "questions", "recent_documents", "recent_questions", "admin",
    }
    assert d["scope"]["unlimited"] is True, "管理员是不受限"
    assert d["admin"] is not None, "管理员能拿到系统级指标"


def test_overview_admin_block_is_null_for_employee(client, make_user):
    """员工拿到的 admin 段是 **null**（不是 0）—— 前端据此整块不渲染。"""
    emp = make_user("employee")
    r = client.get("/api/stats/overview", headers=emp["headers"])
    assert r.json()["code"] == 0
    d = r.json()["data"]
    assert d["admin"] is None
    assert d["scope"]["unlimited"] is False, "员工不是不受限"


def test_overview_counts_only_own_questions(client, make_user, monkeypatch):
    """问答数只算自己的：别人的提问量对他没有意义。"""
    from app.rag import llm as llm_module

    a = make_user("employee")
    b = make_user("employee")

    # 让问答不需要真模型：直接 stub chat
    monkeypatch.setattr(
        llm_module, "chat",
        lambda *args, **kwargs: {"answer": "答案", "usage": {"total_tokens": 1}, "finish_reason": "stop"},
    )

    client.post("/api/chat", json={"query": "甲的问题"}, headers=a["headers"])

    ra = client.get("/api/stats/overview", headers=a["headers"]).json()["data"]
    rb = client.get("/api/stats/overview", headers=b["headers"]).json()["data"]
    assert ra["questions"]["total"] == 1, "自己的提问要算进去"
    assert rb["questions"]["total"] == 0, "不能把别人的提问算到自己头上"


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


# ==================== 并发闸门：只能用静态断言钉住 ====================


def test_count_admins_for_update_emits_row_lock():
    """🔴 `for_update=True` 必须在 MySQL 上真的编译出 `FOR UPDATE`。

    ⚠️ **这条修复在本项目的测试环境无法用行为验证**：SQLite 不支持
    `SELECT ... FOR UPDATE`，SQLAlchemy 会**静默忽略**它（方言能力开关），
    所以"两个管理员并发互降导致归零"这个场景在测试里跑不出来 ——
    跑出来的永远是串行、永远通过。

    因此退而求其次：直接拿 service 里那条语句去 `compile(mysql)`，
    断言 SQL 里有 `FOR UPDATE`。这不是行为验证，但能防止有人在重构时
    把这行悄悄删掉 —— 那种改动会让测试**全绿**而线上少一道并发保护。
    """
    from sqlalchemy.dialects import mysql

    from app.services import user_service

    captured = []

    class _Spy:
        def scalar(self, stmt):
            captured.append(stmt)
            return 0

    user_service.count_admins(_Spy(), for_update=True)
    sql = str(captured[0].compile(dialect=mysql.dialect()))
    assert "FOR UPDATE" in sql, f"没生成行锁，并发互降会绕过闸门：{sql}"

    # 不开锁时不该带 FOR UPDATE（普通计数不需要锁，锁住反而增加争用）
    captured.clear()
    user_service.count_admins(_Spy(), for_update=False)
    sql_plain = str(captured[0].compile(dialect=mysql.dialect()))
    assert "FOR UPDATE" not in sql_plain
