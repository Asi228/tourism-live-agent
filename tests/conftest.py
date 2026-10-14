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
from app.core.security import hash_password
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
        # ⚠️ **幂等插入**，不能直接 add：`client` 是 session 级的，只在**首次被请求**时
        # 初始化；而 `_fresh_tables`（function 级 autouse）每个用例都会重建角色表。
        # 若某个不请求 client 的用例（如纯限流测试）先跑过，表里已经有角色了，
        # 这里再 add 一次就会撞 `UNIQUE constraint failed: roles.code` —— 而且报在
        # setup 阶段，看上去像"某个用例坏了"，实际是 fixture 顺序问题。
        # （与 `init_db._seed_roles` 同一做法：有就跳过。）
        existing = {
            code for (code,) in db.execute(select(Role.code)).all()
        }
        for item in DEFAULT_ROLES:
            if item["code"] not in existing:
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


@pytest.fixture(autouse=True)
def _guard_real_milvus(monkeypatch, request):
    """🔴 测试绝不能碰真 Milvus 的**写/删** —— 一律换成哑元。

    背景（2026-10-14 事故，已实测复现）：
    `test_documents.py` / `test_knowledge.py` / `test_regressions.py` 里有若干用例
    调"删文档 / 删知识库"接口，而后端的清向量函数（`_purge_vectors`）**真连 Milvus**；
    测试库（SQLite）的 id 从 1 开始，**与真库 id 撞车** ——
    结果：每跑一次全量测试，真库 `doc_id=1` / `kb_id=1` 的向量就被真删一次。
    "数据又丢了"就是这么来的（当天跑三次全量 → 删三次，而后只能重新向量化）。

    防护语义：`upsert` / `delete_by_document` / `delete_by_kb` / `flush` / `ensure_loaded`
    走哑元（不执行真操作、返回无害值）。**只读函数（search / count / list_*）不拦** ——
    它们不产生破坏，拦了反而让"检索路径"的用例失去意义。

    放行条件：`tests/test_vectors_integration.py`（唯一真连 Milvus 的集成测试，
    用 9 亿高位隔离 id、跑完即删）—— 真连是它存在的意义，mock 了就没意义了。
    注意放行**按文件**而不是按环境变量：否则"设了 RUN_MILVUS_INTEGRATION=1 跑全量"
    会让所有测试一起失去防护。

    防退用例：`tests/test_regressions.py::test_milvus_destructive_calls_are_guarded`
    （断言哑元标记还在 —— 谁删掉这段防护，它会立刻红）。
    """
    node_path = getattr(request.node, "path", None) or request.node.fspath
    if str(node_path).endswith("test_vectors_integration.py"):
        # 集成测试真连；它自己用高位隔离 id，不会碰真实数据
        yield
        return

    from app.config import settings as settings_mod
    from app.rag import vector_store

    def _guard(name: str, ret):
        def fake(*args, **kwargs):
            return ret

        # 标记：防退用例靠它识别"此刻挂着的是哑元而不是真实现"
        fake._is_milvus_guard = True
        fake._guard_name = name
        return fake

    def _fake_upsert(rows):
        # 真实现返回"成功写入的条数"，哑元保持同构，调用方的日志/断言不会失真
        return len(rows)

    _fake_upsert._is_milvus_guard = True
    _fake_upsert._guard_name = "upsert"

    monkeypatch.setattr(vector_store, "upsert", _fake_upsert)
    # 删返回 0（= "没删任何东西"）是最诚实的中性值：
    # 调用方只在 removed 非零时打日志，返回 0 不会伪造"已清理"的记录
    monkeypatch.setattr(vector_store, "delete_by_document", _guard("delete_by_document", 0))
    monkeypatch.setattr(vector_store, "delete_by_kb", _guard("delete_by_kb", 0))
    # flush / load 同样是**对共享实例改状态**的操作（2026-10-14 新加的这两条）。
    # 不拦的话：每跑一次向量化用例，就真去 flush / load 一次真库的 collection。
    monkeypatch.setattr(vector_store, "flush", _guard("flush", True))
    monkeypatch.setattr(vector_store, "ensure_loaded", _guard("ensure_loaded", True))

    # ---- Embedding（硅基流动 API）：同一条理由的延伸 —— 测试绝不打花真钱的路径 ----
    # 防的是"漏挂 mute_embedding 的用例"：以前它会真发 HTTP（计费），
    # 现在返回固定维度向量 —— 对断言无影响（没有用例依赖真 embedding 的数值）。
    from app.rag import embedding as embedding_mod

    dim = settings_mod.EMBEDDING_DIM

    def _fake_embed_texts(texts):
        return [[0.01] * dim for _ in texts]

    def _fake_embed_query(text):
        return [0.01] * dim

    _fake_embed_texts._is_milvus_guard = True
    _fake_embed_texts._guard_name = "embed_texts"
    _fake_embed_query._is_milvus_guard = True
    _fake_embed_query._guard_name = "embed_query"
    monkeypatch.setattr(embedding_mod, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(embedding_mod, "embed_query", _fake_embed_query)
    yield


@pytest.fixture
def allow_register(monkeypatch):
    """把自助注册打开 —— **只有真的在测注册链路时才用**。

    注册默认是关的（`AUTH_ALLOW_REGISTER=False`），所以绝大多数用例跑的是
    "线上形态"。要测注册本身，显式声明这个 fixture，比在 conftest 里全局打开好：
    全局打开会让"注册被关掉"这件事永远没有用例守着。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_ALLOW_REGISTER", True)
    yield


@pytest.fixture
def make_user(client):
    """造一个用户并返回它的登录凭据。

    ⚠️ **刻意不走注册接口**：注册现在是**默认可关闭**的业务开关
    （`AUTH_ALLOW_REGISTER`），测试工厂如果依赖它，那么"改一个部署开关"
    就会波及 100 多条毫不相干的用例。这里直接写库 ——

    工厂的职责是"造出一份合法的测试数据"，不是"顺带验证注册链路"；
    注册链路由 `tests/test_auth.py:test_register_login_and_me` 专门守着。

    `role_code` 直接在建的时候就指定（以前是"先注册成 employee 再改 role_id"，
    现在是同一件事但更短）。
    """

    def _make(role_code: str = "employee") -> dict:
        username = "u" + uuid.uuid4().hex[:12]
        password = "secret123"

        with TestSessionLocal() as db:
            role = db.scalar(select(Role).where(Role.code == role_code))
            assert role is not None, f"角色 {role_code} 不存在"
            user = User(
                username=username,
                email=f"{username}@example.com",
                password_hash=hash_password(password),
                role_id=role.id,
            )
            db.add(user)
            db.commit()
            user_id = user.id

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
