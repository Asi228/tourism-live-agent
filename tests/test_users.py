"""用户列表接口：权限边界 + 授权闭环能不能只靠接口完成。"""

import uuid


def _kb_name() -> str:
    # 测试库整个 session 共用，名字必须唯一，否则第二个跑到的用例会被"名称已存在"挡住
    return "kb-" + uuid.uuid4().hex[:10]


def test_employee_cannot_list_users(client, make_user):
    emp = make_user("employee")
    r = client.get("/api/users", headers=emp["headers"])
    assert r.json()["code"] == 40300, r.json()


def test_users_requires_login(client):
    assert client.get("/api/users").json()["code"] == 40100


def test_admin_lists_users_without_secrets(client, make_user):
    admin = make_user("admin")
    emp = make_user("employee")

    r = client.get("/api/users", headers=admin["headers"])
    assert r.json()["code"] == 0, r.json()
    item = next(u for u in r.json()["data"] if u["id"] == emp["id"])

    assert item["username"] == emp["username"]
    assert item["role"]["code"] == "employee"
    assert item["is_active"] is True
    # 这个接口只给授权用的最小信息，邮箱和密码哈希都不能出现
    assert set(item.keys()) == {"id", "username", "is_active", "role"}


def test_keyword_filters_users(client, make_user):
    admin = make_user("admin")
    target = make_user("employee")

    r = client.get(f"/api/users?keyword={target['username']}", headers=admin["headers"])
    ids = [u["id"] for u in r.json()["data"]]
    assert ids == [target["id"]]

    r2 = client.get("/api/users?keyword=zzz_不存在的名字", headers=admin["headers"])
    assert r2.json()["code"] == 0
    assert r2.json()["data"] == []


def test_grant_flow_works_with_api_only(client, make_user):
    """授权知识库成员所需的 user_id 必须能只靠接口拿到 —— 否则 Phase 3 的功能闭不了环。"""
    admin = make_user("admin")
    emp = make_user("employee")

    kb = client.post(
        "/api/knowledge", json={"name": _kb_name()}, headers=admin["headers"]
    ).json()
    assert kb["code"] == 0, kb
    kb_id = kb["data"]["id"]

    # 员工先看不到
    assert kb_id not in [
        i["id"] for i in client.get("/api/knowledge", headers=emp["headers"]).json()["data"]
    ]

    found = client.get(f"/api/users?keyword={emp['username']}", headers=admin["headers"]).json()
    user_id = found["data"][0]["id"]

    grant = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": user_id, "permission": "read"},
        headers=admin["headers"],
    ).json()
    assert grant["code"] == 0, grant

    after = client.get("/api/knowledge", headers=emp["headers"]).json()["data"]
    assert [i for i in after if i["id"] == kb_id][0]["my_permission"] == "read"
