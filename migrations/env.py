"""Alembic 运行环境（Phase 11）。

与默认模板有两处刻意的差异：

1. **连接串不从 `alembic.ini` 读，直接取 `settings.mysql_url`。**
   ① 密码里只要出现 `%` 就会和 ini 的插值语法打架（configparser 把 `%x` 当引用），
   从 Python 侧注入 engine 就绕开了这个坑；
   ② 迁移与服务用的是**同一个连接串**，不存在"迁移连 A 库、服务连 B 库"的错配。

2. **必须 `import app.models`。**
   只 import `Base` 的话 `Base.metadata` 是空的 —— 那样 autogenerate 会认为
   "库里的表全是多余的"，生成一份把表全删掉的迁移。这是最贵的一类误操作，
   所以下面那行 import 不是可有可无的顺手操作，而是这个文件能安全运行的前提。

`compare_type=True`：默认只比"列名在不在"，列类型改了不会 diff 出来。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  # ⚠️ 见文件头第 2 条：删了会让 autogenerate 想删光所有的表
from app.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式（`alembic upgrade head --sql`）：只吐 SQL，不连库。"""
    context.configure(
        url=settings.mysql_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：真连库执行。NullPool —— 迁移是一锤子买卖，不需要连接池。"""
    connectable = create_engine(settings.mysql_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
