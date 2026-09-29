"""pytest 公共夹具。

用 SQLite 内存库跑测试，绝不碰你的 MySQL：
通过 dependency_overrides 把 get_db 换成测试 session，
被测代码一行都不用改。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import create_app
from app.db.base import Base
from app.db.init_db import DEFAULT_ROLES
from app.db.session import get_db
from app.models import Role

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
