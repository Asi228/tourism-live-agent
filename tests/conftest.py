"""pytest 公共夹具。

用 SQLite 内存库跑测试，绝不碰你的 MySQL：
通过 dependency_overrides 把 get_db 换成测试 session，
被测代码一行都不用改。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import create_app
from app.db.base import Base
from app.db.init_db import DEFAULT_ROLES
from app.db.session import get_db
from app.models import Role, User

# "sqlite://" = 内存数据库。
# StaticPool + check_same_thread=False 让所有连接共用同一个内存库（否则每个连接看到的是空库）
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="session")
def client():
    # 建表（导入 app.models 时 Role / User 已注册到 Base.metadata）
    Base.metadata.create_all(bind=test_engine)
    with TestSessionLocal() as db:
        for item in DEFAULT_ROLES:
            db.add(Role(**item))
        db.commit()

    app = create_app()

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不用 with，就不会触发 lifespan；建表在真实环境由 main.py 的 init_db() 负责
    return TestClient(app)


@pytest.fixture
def session_factory():
    """直接拿测试 Session 用，验证事务边界这类不经过 HTTP 的行为。"""
    return TestSessionLocal


@pytest.fixture(autouse=True)
def _fresh_tables():
    """⚠️ 每个用例都从**干净的表**开始。

    以前没有这个 fixture，所有用例共享库里的累积数据 —— 于是断言只能写成
    "调用前后的差值"，而差值断言一旦有人中途多写一条，报错就变成
    "应差 2 实差 3"，归因线索全丢。在这里清一次比在每条断言里绕便宜得多。

    默认角色一并重建：`drop_all` 会把 `client` 那个 session 级 fixture 建好的
    角色表内容带走，所以每次重建都要把它放回去（注册/登录都依赖它）。
    """
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    with TestSessionLocal() as db:
        for item in DEFAULT_ROLES:
            db.add(Role(**item))
        db.commit()
    yield


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    """默认关掉限流。

    绝大多数用例验证的是别的行为，不该被"每分钟 N 次"的配额干扰
    （测试会反复调 /api/chat，很容易撞上限）。
    专测限流的用例自己把开关和配额打开 —— 见 tests/test_limiter.py。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    yield


@pytest.fixture
def make_user(client):
    """造一个用户并返回它的登录凭据。

    注册接口只能造出 employee；需要 admin 时直接在测试库里改 role_id
    （真实环境没有开放提权接口，这是按设计来的）。
    """

    def _make(role_code: str = "employee") -> dict:
        username = "u" + uuid.uuid4().hex[:12]
        password = "secret123"
        payload = {
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        }
        r = client.post("/api/auth/register", json=payload)
        assert r.json()["code"] == 0, r.json()
        user_id = r.json()["data"]["id"]

        if role_code != "employee":
            with TestSessionLocal() as db:
                role = db.scalar(select(Role).where(Role.code == role_code))
                assert role is not None, f"角色 {role_code} 不存在"
                user = db.get(User, user_id)
                user.role_id = role.id
                db.commit()

        token = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        ).json()["data"]["access_token"]

        return {
            "id": user_id,
            "username": username,
            "password": password,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _make
