"""迁移与模型是否同步 —— drift 检查（Phase 11）。

**为什么必须有这一条**：迁移是"照历史改表"、模型是"现在表长什么样"。
只要有人改了模型却忘了 `alembic revision --autogenerate`，
`autogenerate` 的 diff 下次仍然显示"已同步"（它比的是**数据库 vs 模型**，
不是**迁移 vs 模型**）—— 迁移就成了摆设，直到某天在别人的机器上炸开。
这条用例比的就是"模型 vs 库结构"，是这件事唯一的自动防线。

**哪些要连真实 MySQL**：需要建/读库的那两条（drift 比对、版本前置检查）默认跳过 ——
测试库是 SQLite，类型反射规则不同，拿它做比对会得到一堆假差异。
口径与另外两个集成用例一致：`RUN_MYSQL_INTEGRATION=1` 才跑。
纯逻辑的两条（迁移链、缺表报错）不需要数据库，**永远跑**。
"""

import os

import pytest

requires_mysql = pytest.mark.skipif(
    os.getenv("RUN_MYSQL_INTEGRATION") != "1",
    reason="需要真实 MySQL：设 RUN_MYSQL_INTEGRATION=1 才跑",
)


def _alembic_config():
    from alembic.config import Config

    return Config("alembic.ini")


def test_migration_chain_has_single_head():
    """迁移链必须解析出唯一的 head（多条 head = 分叉了，升级会直接报错）。"""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, f"迁移出现分叉，heads={heads}"


def test_missing_tables_raise_instead_of_silently_creating(monkeypatch):
    """启动时缺表必须**明确报错**，不许偷偷 `create_all`。

    这是"schema 唯一权威是迁移"这条口径的守卫：项目曾经按 `APP_ENV` 让 dev/test
    用 `create_all` 兜底，而那条路会造出一个**没有 `alembic_version` 记录的库**，
    之后第一次 `alembic upgrade head` 必撞 `table already exists`。
    谁要是把 `create_all` 加回来，这条会挂。

    不连数据库：`inspect` 被换成返回空表清单的替身，`create_all` 也被替换成"记账"。
    """
    from app.db import init_db as mod

    class _EmptyInspector:
        def get_table_names(self):
            return []

    called: list = []
    monkeypatch.setattr(mod, "inspect", lambda _engine: _EmptyInspector())
    # 把 create_all 换成记账函数：只要有人把它加回启动路径，这里就会留下记录。
    # （不换的话它会真跑到 MySQL 上 —— checkfirst 会让它是空操作，于是这条用例反而抓不住。）
    monkeypatch.setattr(mod.Base.metadata, "create_all", lambda *a, **k: called.append(k))

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        mod._ensure_schema()

    assert called == [], "启动路径不该再出现 create_all（建表的唯一权威是迁移）"


def _assert_database_is_at_head() -> None:
    """库必须先升到 head —— **先判这个，再报 diff**。

    为什么顺序重要：`compare_metadata` 比的是「模型 vs 库」，所以一个**忘了
    `alembic upgrade head`** 的库会得到一长串"结构不一致"，看起来像模型写错了，
    真因却只是"库没升上去"。这是别人 clone 下这个仓库后**第一个会撞的红**，
    所以把版本前置判断放在 diff 之前，让它变成一句能直接照做的提示。
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.db.session import engine

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()

    assert current == head, (
        f"库的迁移版本是 {current!r}，但代码里的 head 是 {head!r} —— "
        "请先执行 `alembic upgrade head`，再跑这条 drift 检查"
    )


@requires_mysql
def test_models_and_migrations_are_in_sync():
    """`alembic upgrade head` 之后，模型与库结构必须**零差异**。

    这条不会自己升级，它只做比对；没升到 head 会先给出"请先 upgrade"的提示。
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.models  # noqa: F401  # 必须导入，否则 metadata 是空的（比对会假通过）
    from app.db.base import Base
    from app.db.session import engine

    _assert_database_is_at_head()  # 先判版本：给出能照做的提示，而不是一长串 diff

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)

    assert diff == [], (
        "模型与数据库结构不一致（改了模型却没生成迁移？）:\n"
        + "\n".join(f"  - {d}" for d in diff)
    )
