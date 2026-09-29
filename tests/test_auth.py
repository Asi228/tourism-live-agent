"""认证模块测试：注册 / 登录 / JWT / 当前用户。

重点验核心业务流程，不堆无意义用例。
"""

import uuid

from app.core.security import hash_password, verify_password


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


def test_register_login_and_me(client):
    """完整链路：注册 → 重复注册被拒 → 密码错被拒 → 登录拿 token → 带 token 取当前用户。"""
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
