"""用户管理接口的测试：列表 / 改角色 / 成员授权 / 开通账号 / 停用启用 / 重置密码。

从 `test_user_management.py` 拆出来的 —— 那个文件一度装着四块功能、924 行，
找一条用例只能靠 grep。现在按"一个功能一个文件"分开：

  · 概览统计  → `test_stats.py`
  · 审计接口  → `test_audit.py`

⚠️ 这里所有用例都会写库（改角色 / 开号 / 停用 / 重置密码都是**写入型**管理动作），
权限闸门与审计留痕的断言也在其中。
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


# ==================== 开通账号（POST /users） ====================


def _new_payload(**over):
    """一份合法的建号入参。"""
    import uuid

    name = "n" + uuid.uuid4().hex[:10]
    base = {"username": name, "email": f"{name}@example.com", "password": "secret123"}
    base.update(over)
    return base


def test_admin_can_create_user_with_role(client, make_user):
    """管理员开号可以**一开始就指定角色** —— 否则建完还得再调一次改角色，
    中间那一步"新账号已经有 employee 权限"是个没必要的窗口。"""
    admin = make_user("admin")
    payload = _new_payload(role_code="admin")

    r = client.post("/api/users", json=payload, headers=admin["headers"])
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["role"]["code"] == "admin"
    assert body["data"]["is_active"] is True

    # 能真的登录（密码被正确哈希了，不是随手存的）
    login = client.post(
        "/api/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    ).json()
    assert login["code"] == 0, login


def test_employee_cannot_create_user(client, make_user):
    """开号是管理动作 → require_admin 挡住。"""
    emp = make_user("employee")
    r = client.post("/api/users", json=_new_payload(), headers=emp["headers"])
    assert r.json()["code"] == ErrorCode.FORBIDDEN, r.json()


def test_create_user_rejects_duplicate_username_and_email(client, make_user):
    """用户名 / 邮箱唯一性 —— 与注册**共用同一份检查**，报错文案也一致。"""
    admin = make_user("admin")
    first = _new_payload()
    assert client.post(
        "/api/users", json=first, headers=admin["headers"]
    ).json()["code"] == 0

    dup_name = client.post(
        "/api/users", json=_new_payload(username=first["username"]), headers=admin["headers"]
    ).json()
    assert dup_name["code"] == ErrorCode.PARAM_ERROR
    assert "用户名" in dup_name["message"]

    dup_mail = client.post(
        "/api/users", json=_new_payload(email=first["email"]), headers=admin["headers"]
    ).json()
    assert dup_mail["code"] == ErrorCode.PARAM_ERROR
    assert "邮箱" in dup_mail["message"]


def test_create_user_rejects_unknown_role(client, make_user):
    """角色不存在 → 40000（入参不合法），不是 40400（那是"找不到用户"）。"""
    admin = make_user("admin")
    r = client.post(
        "/api/users", json=_new_payload(role_code="superman"), headers=admin["headers"]
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR, r.json()


def test_create_user_reuses_register_field_rules(client, make_user):
    """建号与注册的字段规则**必须一致**（username 字符集 / 密码长度）。

    做法是让 `UserCreateRequest` 继承 `RegisterRequest` —— 这条用例钉住这个继承，
    防止将来有人"为了放宽管理端的限制"另写一份校验，于是同名的规矩在两处漂移。
    """
    admin = make_user("admin")
    r = client.post(
        "/api/users",
        json={"username": "张三", "email": "bad-email", "password": "1"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR, r.json()


def test_create_user_writes_audit_without_password(client, make_user):
    """开号必须留痕（唯一一条"创造出新身份"的记录），且 detail 里**绝不能有密码**。"""
    admin = make_user("admin")
    payload = _new_payload(role_code="admin")
    created = client.post("/api/users", json=payload, headers=admin["headers"]).json()

    r = client.get("/api/audit", headers=admin["headers"]).json()
    row = [x for x in r["data"]["items"] if x["action"] == "user.create"][0]
    assert row["target_id"] == created["data"]["id"]
    assert row["target_name"] == payload["username"]
    assert row["action_text"] == "开通账号"
    assert row["detail"]["role_code"] == "admin"
    # 🔴 密码绝不能出现在审计明细里
    assert "password" not in str(row["detail"])
    assert payload["password"] not in str(row)


# ==================== 停用 / 启用（PATCH /users/{id}/status） ====================


def test_disable_blocks_login_and_enable_restores_it(client, make_user):
    """停用 → 登录被拒；启用 → 又能登录。"""
    admin = make_user("admin")
    victim = make_user("employee")

    r = client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["changed"] is True

    login = client.post(
        "/api/auth/login",
        json={"username": victim["username"], "password": victim["password"]},
    ).json()
    assert login["code"] == ErrorCode.FORBIDDEN, login
    assert "禁用" in login["message"]

    # 启用回去
    assert client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": True},
        headers=admin["headers"],
    ).json()["code"] == 0
    assert client.post(
        "/api/auth/login",
        json={"username": victim["username"], "password": victim["password"]},
    ).json()["code"] == 0


def test_disabled_user_token_dies_immediately(client, make_user):
    """🔴 **已签发的 token 在停用后立刻失效** —— 这是"停用"能不能算数的关键。

    JWT 是无状态的，服务端**不吊销**已发出的 token（这一点 README「登出语义」
    那条已经写明）。但账号状态不同：`get_current_user` 每次都回数据库查
    `is_active`，所以停用能立刻生效、不必等 token 过期。

    没有这一条，"停用"就只是"下次登录进不来"，手上那张票还能用一整天。
    """
    admin = make_user("admin")
    victim = make_user("employee")

    # 停用前，手里这张票是好用的
    assert client.get("/api/auth/me", headers=victim["headers"]).json()["code"] == 0

    client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    )

    # 停用后，**同一张票**立刻不好用
    r = client.get("/api/auth/me", headers=victim["headers"]).json()
    assert r["code"] == ErrorCode.FORBIDDEN, r
    assert "禁用" in r["message"]

    # 而且不只是 /me —— 业务接口一样挡住
    r = client.get("/api/knowledge", headers=victim["headers"]).json()
    assert r["code"] == ErrorCode.FORBIDDEN, r


def test_admin_cannot_disable_self(client, make_user):
    """🔴 不能停自己 —— 与"不能改自己的角色"同理，防的是把自己锁在系统外。"""
    admin = make_user("admin")
    r = client.patch(
        f"/api/users/{admin['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR, r.json()
    assert "自己" in r.json()["message"]


def test_disable_is_idempotent(client, make_user):
    """重复停用返回 `changed=false`，不是错误 —— 前端据此提示"未发生变化"。"""
    admin = make_user("admin")
    victim = make_user("employee")

    client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    )
    again = client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    ).json()
    assert again["code"] == 0
    assert again["data"]["changed"] is False


def test_disable_writes_audit_with_readable_snapshot(client, make_user):
    """停用要留痕，且存的是 `active/disabled` 而不是布尔值。"""
    admin = make_user("admin")
    victim = make_user("employee")

    client.patch(
        f"/api/users/{victim['id']}/status",
        json={"is_active": False},
        headers=admin["headers"],
    )
    r = client.get("/api/audit", headers=admin["headers"]).json()
    row = [x for x in r["data"]["items"] if x["action"] == "user.status_change"][0]
    assert row["target_id"] == victim["id"]
    assert row["detail"] == {"from": "active", "to": "disabled"}
    assert row["action_text"] == "停用 / 启用账号"


def test_cannot_disable_the_last_admin(session_factory, make_user):
    """🔴 不能让 admin 归零（停用这个方向）。

    与改角色的同名约束一样，**在 HTTP 层触发不到**（要停用 A 就得有 admin B 操作，
    那时至少两个 admin），所以绕过接口层直接调 service 验证闸门本身。

    ⚠️ actor 用 **employee**：`change_status` 不检查调用者权限（那是 `require_admin`
    在接口层的活），所以这里**不能让 actor 也是 admin** —— 否则 `count_admins` 变成 2，
    闸门就不生效了，用例会"通过得莫名其妙"。先 `assert count_admins == 1` 钉住前提。
    """
    from app.core.exceptions import AppException
    from app.models.user import User
    from app.services import user_service

    only_admin = make_user("admin")            # 系统里唯一的 admin
    non_admin_actor = make_user("employee")    # 以非 admin 身份直接调 service

    with session_factory() as db:
        actor = db.get(User, non_admin_actor["id"])
        assert user_service.count_admins(db) == 1
        with pytest.raises(AppException) as ei:
            user_service.change_status(
                db, actor=actor, target_id=only_admin["id"], is_active=False
            )
        assert "至少要保留一个可用的管理员" in ei.value.message


# ==================== 重置密码（PATCH /users/{id}/password） ====================


def _login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()


def test_admin_can_reset_password(client, make_user):
    """重置后：**新密码能登录，旧密码不行**。"""
    admin = make_user("admin")
    victim = make_user("employee")
    assert _login(client, victim["username"], victim["password"])["code"] == 0

    r = client.patch(
        f"/api/users/{victim['id']}/password",
        json={"new_password": "brand-new-pw"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["is_self"] is False

    assert _login(client, victim["username"], "brand-new-pw")["code"] == 0
    assert _login(client, victim["username"], victim["password"])["code"] == 40100


def test_reset_password_invalidates_old_tokens(client, make_user):
    """🔴 旧 token 立刻失效 —— 这是"改密码"能不能算数的关键。

    JWT 无状态、服务端不吊销，所以如果不做别的，改完密码旧 token 还能用到过期
    （默认 24 小时）。而改密码最常见的动机恰恰是"凭证可能泄露了" ——
    旧 token 还活着，等于没改。

    实现是 token 里带**密码指纹**（`security.password_fingerprint`）：
    bcrypt 每次换盐 → 哈希变 → 指纹变 → 旧 token 的指纹对不上 → 拒。
    """
    admin = make_user("admin")
    victim = make_user("employee")

    # 重置前：这张票好用
    assert client.get("/api/auth/me", headers=victim["headers"]).json()["code"] == 0

    client.patch(
        f"/api/users/{victim['id']}/password",
        json={"new_password": "brand-new-pw"},
        headers=admin["headers"],
    )

    # 重置后：**同一张票**立刻不好用
    r = client.get("/api/auth/me", headers=victim["headers"]).json()
    assert r["code"] == 40100, r
    assert "重新登录" in r["message"]

    # 重新登录拿到的新票是好的
    fresh = _login(client, victim["username"], "brand-new-pw")
    assert fresh["code"] == 0
    new_headers = {"Authorization": f"Bearer {fresh['data']['access_token']}"}
    assert client.get("/api/auth/me", headers=new_headers).json()["code"] == 0


def test_login_again_does_not_invalidate_other_sessions(client, make_user):
    """⚠️ 反面对照：**光重新登录不该踢掉别的设备**。

    指纹取自 `password_hash`，不是"签发时间" —— 所以同一密码登录两次得到的
    两个 token **都有效**（多设备登录是正常需求）。
    没有这一条的话，把指纹写成"最后登录时间"之类的实现也能让上面那些用例全绿。

    ⚠️ **别断言"两张票的字符串不同"**：JWT 的 `iat` 只精确到秒，同一秒内、同一用户、
    同一密码指纹签出来的 payload 逐字相同 → HMAC 签名也相同 → token 就是**同一个字符串**。
    （不是缺陷：两者等价，谁用都一样；隔一秒再登录就会不同。）
    所以这里只验"两张都有效"这个真正的不变量。
    """
    user = make_user("employee")
    token_a = user["headers"]["Authorization"]
    second = _login(client, user["username"], user["password"])
    token_b = f"Bearer {second['data']['access_token']}"

    assert client.get("/api/auth/me", headers={"Authorization": token_a}).json()["code"] == 0
    assert client.get("/api/auth/me", headers={"Authorization": token_b}).json()["code"] == 0


def test_reset_own_password_ends_current_session(client, make_user):
    """重置**自己**的密码：允许（不构成自锁），但当前会话会立刻结束。

    返回 `is_self=true` 就是给前端用的 —— 让它主动登出并跳登录页，
    而不是让用户看到"操作成功"之后下一个请求莫名 401。
    """
    admin = make_user("admin")

    r = client.patch(
        f"/api/users/{admin['id']}/password",
        json={"new_password": "my-new-pw-1"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["is_self"] is True

    # 旧票作废
    assert client.get("/api/auth/me", headers=admin["headers"]).json()["code"] == 40100
    # 新密码可用（否则"允许改自己"就成了自锁）
    assert _login(client, admin["username"], "my-new-pw-1")["code"] == 0
    assert _login(client, admin["username"], admin["password"])["code"] == 40100


def test_employee_cannot_reset_password(client, make_user):
    """重置密码是管理动作 → require_admin 挡住。"""
    emp = make_user("employee")
    other = make_user("employee")

    r = client.patch(
        f"/api/users/{other['id']}/password",
        json={"new_password": "hacked-pw"},
        headers=emp["headers"],
    )
    assert r.json()["code"] == ErrorCode.FORBIDDEN, r.json()
    # 目标用户的密码不受影响
    assert _login(client, other["username"], other["password"])["code"] == 0


def test_reset_password_reuses_register_length_rules(client, make_user):
    """长度规则与注册**共用同一组常量** —— 不能出现"注册要 6 位、重置只要 3 位"。"""
    admin = make_user("admin")
    victim = make_user("employee")

    r = client.patch(
        f"/api/users/{victim['id']}/password",
        json={"new_password": "1"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == ErrorCode.PARAM_ERROR, r.json()


def test_reset_password_writes_audit_without_secrets(client, make_user):
    """审计记"谁重置了谁"，但**密码、哈希、指纹一个都不留**。"""
    admin = make_user("admin")
    victim = make_user("employee")
    client.patch(
        f"/api/users/{victim['id']}/password",
        json={"new_password": "brand-new-pw"},
        headers=admin["headers"],
    )

    rows = client.get("/api/audit", headers=admin["headers"]).json()["data"]["items"]
    row = [x for x in rows if x["action"] == "user.password_reset"][0]
    assert row["target_id"] == victim["id"]
    assert row["target_name"] == victim["username"]
    assert row["action_text"] == "重置密码"
    assert row["detail"] in (None, {})
    # 任何形式的密码派生值都不该出现
    assert "brand-new-pw" not in str(row)
    assert "$2b$" not in str(row)


def test_password_fingerprint_tracks_the_hash():
    """指纹单元行为：同哈希稳定、换哈希就变 —— 它是这套失效机制的基石。"""
    from app.core.security import hash_password, password_fingerprint

    h1 = hash_password("same-pass")
    h2 = hash_password("same-pass")
    assert h1 != h2, "bcrypt 每次换盐，两次哈希必然不同"
    assert password_fingerprint(h1) == password_fingerprint(h1), "同一哈希必须得到同一指纹"
    assert password_fingerprint(h1) != password_fingerprint(h2), "换了哈希就必须换指纹"
    assert len(password_fingerprint(h1)) == 16
