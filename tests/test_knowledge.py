"""知识库模块测试：CRUD + 三层权限模型 + 成员授权。

重点验证权限边界 —— 这是本模块最容易出错的地方。
"""

import uuid

from sqlalchemy import func, select

from app.models import KnowledgeBaseMember, User
from app.services import knowledge_service


def _kb_name() -> str:
    return "kb-" + uuid.uuid4().hex[:10]


def _create_kb(client, headers, name=None, **extra):
    payload = {"name": name or _kb_name(), "description": "测试用", **extra}
    return client.post("/api/knowledge", json=payload, headers=headers)


def _ids(client, headers):
    r = client.get("/api/knowledge", headers=headers)
    assert r.json()["code"] == 0, r.json()
    return [item["id"] for item in r.json()["data"]]


def _perm_of(client, headers, kb_id):
    r = client.get("/api/knowledge", headers=headers)
    for item in r.json()["data"]:
        if item["id"] == kb_id:
            return item["my_permission"]
    return None


# ==================== 创建权限（require_admin 首次生效）====================


def test_employee_cannot_create_kb(client, make_user):
    """普通员工创建知识库必须被拒 —— 这条同时验证了 require_admin 真的接线了。"""
    employee = make_user("employee")
    r = _create_kb(client, employee["headers"])
    assert r.json()["code"] == 40300, r.json()
    assert "管理员" in r.json()["message"]


def test_admin_can_create_kb_and_name_is_unique(client, make_user):
    admin = make_user("admin")
    name = _kb_name()

    r = _create_kb(client, admin["headers"], name=name)
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["name"] == name
    assert r.json()["data"]["my_permission"] == "admin"

    # 同名再建一次 → 参数错误
    r2 = _create_kb(client, admin["headers"], name=name)
    assert r2.json()["code"] == 40000


def test_create_requires_login(client):
    r = client.post("/api/knowledge", json={"name": _kb_name()})
    assert r.json()["code"] == 40100


# ==================== 隔离：看不到未授权的库 ====================


def test_unrelated_employee_cannot_see_private_kb(client, make_user):
    admin = make_user("admin")
    outsider = make_user("employee")

    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    # 列表里看不到
    assert kb_id not in _ids(client, outsider["headers"])
    # 详情返回 40400 而不是 40300 —— 不暴露"这个 id 存在"
    r = client.get(f"/api/knowledge/{kb_id}", headers=outsider["headers"])
    assert r.json()["code"] == 40400, r.json()


def test_no_access_and_nonexistent_are_indistinguishable(client, make_user):
    """防枚举必须连文案一起防：只统一 code 的话，比对 message 照样能探测。"""
    admin = make_user("admin")
    outsider = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    denied = client.get(f"/api/knowledge/{kb_id}", headers=outsider["headers"]).json()
    missing = client.get("/api/knowledge/99999999", headers=outsider["headers"]).json()
    assert denied["code"] == missing["code"] == 40400
    assert denied["message"] == missing["message"], "无权访问与不存在不能返回两句话"

    # 成员列表接口同样不能漏
    denied_m = client.get(f"/api/knowledge/{kb_id}/members", headers=outsider["headers"]).json()
    missing_m = client.get("/api/knowledge/99999999/members", headers=outsider["headers"]).json()
    assert denied_m["message"] == missing_m["message"]


def test_grant_read_then_visible(client, make_user):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    assert kb_id not in _ids(client, emp["headers"])

    r = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["permission"] == "read"
    assert r.json()["data"]["username"] == emp["username"]

    # 现在能看到了
    assert kb_id in _ids(client, emp["headers"])
    assert _perm_of(client, emp["headers"], kb_id) == "read"
    # 详情也能看
    r = client.get(f"/api/knowledge/{kb_id}", headers=emp["headers"])
    assert r.json()["code"] == 0


def test_read_permission_cannot_modify(client, make_user):
    """read 权限只能看，不能改。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )

    r = client.put(
        f"/api/knowledge/{kb_id}", json={"description": "偷改"}, headers=emp["headers"]
    )
    assert r.json()["code"] == 40300, r.json()


def test_public_kb_visible_to_everyone(client, make_user):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"], is_public=True).json()["data"]["id"]

    assert kb_id in _ids(client, emp["headers"])
    assert _perm_of(client, emp["headers"], kb_id) == "read"


# ==================== 更新 / 删除 ====================


def test_admin_can_update_kb(client, make_user):
    admin = make_user("admin")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    r = client.put(
        f"/api/knowledge/{kb_id}",
        json={"description": "改过了", "is_public": True},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["description"] == "改过了"
    assert r.json()["data"]["is_public"] is True

    # 没传的字段保持原样
    r2 = client.put(
        f"/api/knowledge/{kb_id}", json={"description": "只改这个"}, headers=admin["headers"]
    )
    assert r2.json()["data"]["is_public"] is True


def test_delete_kb_removes_members(client, make_user, session_factory):
    """删库要连带删成员记录，不能留孤儿行。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )

    with session_factory() as db:
        before = db.scalar(
            select(func.count()).select_from(KnowledgeBaseMember).where(
                KnowledgeBaseMember.knowledge_base_id == kb_id
            )
        )
    assert before == 2  # 创建者 + emp

    r = client.delete(f"/api/knowledge/{kb_id}", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()

    with session_factory() as db:
        after = db.scalar(
            select(func.count()).select_from(KnowledgeBaseMember).where(
                KnowledgeBaseMember.knowledge_base_id == kb_id
            )
        )
    assert after == 0


def test_employee_cannot_delete_kb(client, make_user):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "admin"},
        headers=admin["headers"],
    )
    # 给了 kb admin，删除也应该失败：删除只允许系统 admin
    r = client.delete(f"/api/knowledge/{kb_id}", headers=emp["headers"])
    assert r.json()["code"] == 40300, r.json()


# ==================== 成员管理 ====================


def test_grant_is_idempotent_and_upgrades_permission(client, make_user):
    """重复授权不应产生第二条记录，而是更新权限。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    for perm in ("read", "write", "read"):
        r = client.post(
            f"/api/knowledge/{kb_id}/members",
            json={"user_id": emp["id"], "permission": perm},
            headers=admin["headers"],
        )
        assert r.json()["code"] == 0, r.json()
        assert r.json()["data"]["permission"] == perm

    r = client.get(f"/api/knowledge/{kb_id}/members", headers=admin["headers"])
    member_ids = [m["user_id"] for m in r.json()["data"]]
    assert member_ids.count(emp["id"]) == 1
    assert member_ids.count(admin["id"]) == 1  # 创建者自己被自动写入


def test_list_and_revoke_member(client, make_user):
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "write"},
        headers=admin["headers"],
    )

    r = client.delete(
        f"/api/knowledge/{kb_id}/members/{emp['id']}", headers=admin["headers"]
    )
    assert r.json()["code"] == 0, r.json()
    assert kb_id not in _ids(client, emp["headers"])

    # 再删一次 → 已不是成员
    r2 = client.delete(
        f"/api/knowledge/{kb_id}/members/{emp['id']}", headers=admin["headers"]
    )
    assert r2.json()["code"] == 40400


def test_cannot_grant_nonexistent_user(client, make_user):
    admin = make_user("admin")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    r = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": 99999999, "permission": "read"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 40400


def test_cannot_revoke_owner(client, make_user):
    admin = make_user("admin")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    r = client.delete(
        f"/api/knowledge/{kb_id}/members/{admin['id']}", headers=admin["headers"]
    )
    assert r.json()["code"] == 40000, r.json()


def test_invalid_permission_value_rejected(client, make_user):
    """permission 用 Literal 约束，非法值在参数校验阶段就被拦下。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    r = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "superuser"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 40000, r.json()


def test_non_member_cannot_list_members(client, make_user):
    admin = make_user("admin")
    outsider = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    r = client.get(f"/api/knowledge/{kb_id}/members", headers=outsider["headers"])
    assert r.json()["code"] == 40400, r.json()


# ==================== 给 Phase 5 用的权限过滤接口 ====================


def test_get_accessible_kb_ids(client, make_user, session_factory):
    """admin 返回 None（不受限）；employee 返回具体 id 列表。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    with session_factory() as db:
        admin_user = db.get(User, admin["id"])
        emp_user = db.get(User, emp["id"])
        assert knowledge_service.get_accessible_kb_ids(db, admin_user) is None
        emp_ids = knowledge_service.get_accessible_kb_ids(db, emp_user)

    assert isinstance(emp_ids, list)
    assert kb_id not in emp_ids


# ==================== 本轮修缮的回归位 ====================


def test_revoke_on_public_kb_still_readable(client, make_user):
    """公开库移除成员只删授权记录，不收回可见性 —— is_public 优先。

    这是有意的设计（否则"公开"就名不副实），钉住它是为了：将来若改成
    "黑名单式排除"，这条会先响，逼着改的人写清楚新语义。
    """
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"], is_public=True).json()["data"]["id"]
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )

    r = client.delete(f"/api/knowledge/{kb_id}/members/{emp['id']}", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()

    detail = client.get(f"/api/knowledge/{kb_id}", headers=emp["headers"]).json()
    assert detail["code"] == 0, "公开库移除成员后不该变成不可访问"
    assert detail["data"]["my_permission"] == "read"

    members = client.get(f"/api/knowledge/{kb_id}/members", headers=admin["headers"]).json()["data"]
    assert emp["id"] not in [m["user_id"] for m in members], "授权记录本身必须真的删掉"


def test_write_tier_does_not_grant_kb_metadata_edit(client, make_user):
    """write 覆盖文档读写，不覆盖"改知识库本身"（那仍要 admin）。

    边界与 Phase 4 的接口分工对齐：
      read  → 看知识库/文档/chunk
      write → 上传、删除文档（见 test_documents.py 的权限用例）
      admin → 改知识库元数据、管成员
    """
    admin = make_user("admin")
    emp_read = make_user("employee")
    emp_write = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    for uid, perm in ((emp_read["id"], "read"), (emp_write["id"], "write")):
        client.post(
            f"/api/knowledge/{kb_id}/members",
            json={"user_id": uid, "permission": perm},
            headers=admin["headers"],
        )

    for emp in (emp_read, emp_write):
        assert client.get(f"/api/knowledge/{kb_id}", headers=emp["headers"]).json()["code"] == 0
        r = client.put(f"/api/knowledge/{kb_id}", json={"description": "x"}, headers=emp["headers"])
        assert r.json()["code"] == 40300
        assert client.get(f"/api/knowledge/{kb_id}/members", headers=emp["headers"]).json()["code"] == 40300


def test_dirty_permission_row_does_not_crash(client, make_user, session_factory):
    """成员表里出现非法档位（人工改库/脚本导入）时不能把请求打成 500。"""
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    with session_factory() as db:
        db.add(KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=emp["id"], permission="owner"))
        db.commit()

    items = {i["id"]: i for i in client.get("/api/knowledge", headers=emp["headers"]).json()["data"]}
    assert kb_id not in items, "非法权限值应被忽略，不能让脏行自己生效"

    r = client.get(f"/api/knowledge/{kb_id}", headers=emp["headers"]).json()
    assert r["code"] == 40400, f"脏数据不该把请求打成 {r['code']}"


def test_rename_to_own_name_is_allowed(client, make_user):
    """改名成自己现在的名字不该被判成"名称已存在"（判重要比对 id）。

    注：真库列排序规则是大小写不敏感的，所以 "Tech"→"tech" 这一支只有 MySQL 能实测，
    SQLite 测试环境区分大小写，走不到 same_name.id == kb.id 这个分支。
    """
    admin = make_user("admin")
    name = _kb_name()
    kb_id = _create_kb(client, admin["headers"], name=name).json()["data"]["id"]

    r = client.put(f"/api/knowledge/{kb_id}", json={"name": name}, headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["name"] == name


def test_kb_list_owner_is_exposed(client, make_user):
    """响应带 owner 对象，前端不用再为显示创建者名字单独查一次用户。"""
    admin = make_user("admin")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
    items = {i["id"]: i for i in client.get("/api/knowledge", headers=admin["headers"]).json()["data"]}

    assert items[kb_id]["owner"] == {"id": admin["id"], "username": admin["username"]}
    assert items[kb_id]["owner_id"] == admin["id"]


def test_list_kbs_does_not_query_members_per_kb(client, make_user, session_factory):
    """列表的授权记录必须一次查完。

    判据不是时间而是 SQL 条数：加库数量时成员查询必须保持常数级。
    """
    from sqlalchemy import event

    admin = make_user("admin")
    emp = make_user("employee")
    granted_ids = []
    for _ in range(6):
        kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]
        granted_ids.append(kb_id)
        client.post(
            f"/api/knowledge/{kb_id}/members",
            json={"user_id": emp["id"], "permission": "read"},
            headers=admin["headers"],
        )

    bind = session_factory.kw["bind"]
    member_queries = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        if "knowledge_base_members" in statement:
            member_queries["n"] += 1

    event.listen(bind, "before_cursor_execute", _count)
    try:
        r = client.get("/api/knowledge", headers=emp["headers"])
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    # 测试库是整个 session 共用的，别的项目也可能对这员工开放，所以只断言"这 6 个都在"
    assert set(granted_ids) <= {i["id"] for i in r.json()["data"]}
    # 6 条授权记录：按库查就是 6 次，批量查必须是常数次数
    assert member_queries["n"] <= 3, f"成员表查询次数 {member_queries['n']} 次，出现 N+1"


def test_member_list_never_returns_dirty_permission(client, make_user, session_factory):
    """成员列表不得把库里的非法档位原样吐出去。

    和 _member_perm_map 保持同一口径：非法档位等同于"没有授权记录"，整行略去。
    之所以要钉死：前端（调试台）会把 permission 渲染成 CSS class 名，
    原样透出就等于把未受约束的字符串交给模板层。
    """
    admin = make_user("admin")
    emp = make_user("employee")
    kb_id = _create_kb(client, admin["headers"]).json()["data"]["id"]

    dirty = 'read" onmouseover="window.__x=1'
    with session_factory() as db:
        db.add(
            KnowledgeBaseMember(
                knowledge_base_id=kb_id, user_id=emp["id"], permission=dirty
            )
        )
        db.commit()

    body = client.get(f"/api/knowledge/{kb_id}/members", headers=admin["headers"]).json()
    assert body["code"] == 0, body

    perms = [m["permission"] for m in body["data"]]
    assert dirty not in perms, "脏权限值不能被 API 原样返回"
    assert all(p in ("read", "write", "admin") for p in perms), perms
    assert emp["id"] not in [m["user_id"] for m in body["data"]], "非法档位应整行略去"
