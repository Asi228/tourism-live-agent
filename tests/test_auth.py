"""认证模块测试：注册 / 登录 / JWT / 当前用户。

重点验核心业务流程，不堆无意义用例。
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.models import Role
from app.repositories import user_repository


def _username() -> str:
    """每次生成不同用户名：SQLite 测试库在整轮测试中是共享的。"""
    return "u" + uuid.uuid4().hex[:12]


def test_password_hash_is_not_plaintext():
    """密码必须哈希存储，且能被正确校验。"""
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$2")          # bcrypt 哈希固定以 $2 开头
    assert len(hashed) == 60                # bcrypt 哈希长度固定 60
    assert verify_password("secret123", hashed) is True
    assert verify_password("wrong-pass", hashed) is False
    # 同一个密码两次哈希不同（盐随机）
    assert hash_password("secret123") != hashed


def test_password_hash_handles_garbage():
    """非法哈希不能抛异常，只返回 False。"""
    assert verify_password("x", "not-a-bcrypt-hash") is False
    assert verify_password("x", "") is False


def test_register_login_and_me(client, allow_register):
    """完整链路：注册 → 重复注册被拒 → 密码错被拒 → 登录拿 token → 带 token 取当前用户。

    ⚠️ 要显式要 `allow_register`：注册默认是**关**的（企业里账号该由管理员开），
    这条链路的用例自己把开关打开，而不是让 conftest 全局打开 ——
    全局打开的话，"注册默认关"就没有用例守着了。
    """
    username = _username()
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "secret123",
    }

    # 1. 注册成功，默认角色 employee，且响应里不得出现任何密码字段
    r = client.post("/api/auth/register", json=payload)
    body = r.json()
    assert r.status_code == 200
    assert body["code"] == 0, body
    assert body["data"]["username"] == username
    assert body["data"]["role"]["code"] == "employee"
    assert "password" not in body["data"]
    assert "password_hash" not in body["data"]

    # 2. 用户名重复
    r = client.post("/api/auth/register", json=payload)
    assert r.json()["code"] == 40000

    # 3. 邮箱重复（换个用户名、同邮箱）
    r = client.post(
        "/api/auth/register",
        json={"username": _username(), "email": payload["email"], "password": "secret123"},
    )
    assert r.json()["code"] == 40000

    # 4. 参数不合法：用户名含非法字符 / 密码过短 / 邮箱格式错
    r = client.post(
        "/api/auth/register",
        json={"username": "张三", "email": "bad-email", "password": "1"},
    )
    assert r.json()["code"] == 40000

    # 5. 密码错误
    r = client.post("/api/auth/login", json={"username": username, "password": "wrong"})
    assert r.json()["code"] == 40100

    # 6. 用户不存在 —— 提示必须和"密码错误"完全一致，防止账号枚举
    r = client.post("/api/auth/login", json={"username": _username(), "password": "wrong"})
    assert r.json()["code"] == 40100
    assert r.json()["message"] == "用户名或密码错误"

    # 7. 登录成功
    r = client.post("/api/auth/login", json={"username": username, "password": "secret123"})
    body = r.json()
    assert body["code"] == 0, body
    token = body["data"]["access_token"]
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["expires_in"] > 0
    assert token.count(".") == 2            # JWT 固定三段

    # 8. 带 token 取当前用户
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["username"] == username

    # 9. 不带 token
    r = client.get("/api/auth/me")
    assert r.json()["code"] == 40100

    # 10. 伪造 / 篡改 token
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert r.json()["code"] == 40100
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}x"})
    assert r.json()["code"] == 40100


def test_unhandled_exception_stays_in_envelope(client):
    """未捕获异常必须走统一响应，不能把 traceback 吐给客户端。

    钉住 create_app() 里的 FastAPI(debug=False)：debug=True 时 Starlette 的
    ServerErrorMiddleware 会跳过我们注册的 Exception 处理器，直接返 500 + 原始堆栈，
    既违反"HTTP 统一 200、错误码在 body"的约定，也绕过日志脱敏。
    """

    @client.app.get("/_boom_for_test")
    def _boom():
        raise RuntimeError("内部细节 password=hunter2")

    raw = TestClient(client.app, raise_server_exceptions=False)
    r = raw.get("/_boom_for_test")
    assert r.status_code == 200
    assert r.json()["code"] == 59999
    assert "hunter2" not in r.text
    assert "Traceback" not in r.text


def test_repository_create_does_not_own_transaction(client, session_factory):
    """事务边界在 Service 层：Repository 写完不该自己提交。

    证法是"回滚得掉"：如果 Repository 内部提交了，rollback 就无法撤销这行。
    （不能用"另一个 session 看不见"来证：测试引擎是 StaticPool，
    所有 session 共用同一条 DBAPI 连接，未提交的数据彼此本来就可见。）
    client 参数不直接用，只为触发建表。
    """
    username = _username()
    fake_hash = "$2b$12$" + "x" * 53

    with session_factory() as db:
        role_id = db.scalar(select(Role.id).where(Role.code == "employee"))
        created = user_repository.create(
            db,
            username=username,
            email=f"{username}@example.com",
            password_hash=fake_hash,
            role_id=role_id,
        )
        assert created.id is not None                        # flush 后即可拿到自增主键
        assert db.in_transaction()                           # 事务仍未结束
        assert user_repository.get_by_username(db, username) is not None

        db.rollback()
        assert user_repository.get_by_username(db, username) is None


# ==================== 自助注册开关（AUTH_ALLOW_REGISTER） ====================


def test_allow_register_defaults_to_false():
    """默认值必须是关的 —— 这是"账号由管理员开"这条口径的落点。

    直接断言模型字段的默认值，而不是断言 settings 的当前值：后者可能被
    `.env` 覆盖（本地想试自助注册就会覆盖），而**默认该是关的**这件事
    与部署环境无关。
    """
    from app.config import Settings

    assert Settings.model_fields["AUTH_ALLOW_REGISTER"].default is False


def test_register_is_rejected_when_switch_off(client, monkeypatch):
    """开关关着时，注册接口必须拒绝，且错误码是 40300 而不是 40400。"""
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_ALLOW_REGISTER", False)
    username = _username()

    r = client.post(
        "/api/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "secret123"},
    )
    body = r.json()
    assert body["code"] == 40300, body
    # 403（存在但被策略禁止）而不是 404（让人以为路径写错了）
    assert "管理员" in body["message"]

    # 拒绝要彻底：账号不能真的被建出来
    r = client.post("/api/auth/login", json={"username": username, "password": "secret123"})
    assert r.json()["code"] == 40100


def test_auth_config_exposes_register_switch(client, monkeypatch):
    """`GET /auth/config` 是**公开**的（登录页要用它决定显不显示注册入口）。"""
    from app.config import settings

    r = client.get("/api/auth/config")          # 不带 token
    assert r.json()["code"] == 0
    assert r.json()["data"]["allow_register"] is False

    monkeypatch.setattr(settings, "AUTH_ALLOW_REGISTER", True)
    r = client.get("/api/auth/config")
    assert r.json()["data"]["allow_register"] is True
