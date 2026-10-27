from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_database_url = settings.mysql_url
if _database_url.startswith("sqlite"):
    # 公网作品演示使用单文件 SQLite：数据量很小，而且启动脚本会幂等重建演示数据。
    # check_same_thread=False 是 FastAPI 在线程池中使用同步 Session 的必要条件。
    engine = create_engine(
        _database_url,
        connect_args={"check_same_thread": False},
        echo=settings.SQL_ECHO,
    )
else:
    engine = create_engine(
        _database_url,
        pool_pre_ping=True,                      # 取连接前先探活，避免 MySQL 断连后报 Lost connection
        pool_recycle=3600,                       # 1 小时回收连接，防 MySQL 默认 8h 超时
        pool_size=settings.DB_POOL_SIZE,         # 常驻连接数
        max_overflow=settings.DB_MAX_OVERFLOW,   # 峰值允许临时超出的连接数
        echo=settings.SQL_ECHO,                  # 独立开关，不跟 DEBUG 绑，避免控制台刷满 SQL
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def new_session() -> Session:
    """开一个**独立于请求**的 session。

    给"响应已经开始之后"的路径用：**流式落库**、以及 `done` 之后的收尾维护
    （摘要 + 裁剪）。请求那个 session 在响应体开始迭代**之前**就关了
    （fastapi 0.115.6 的 yield 依赖早退，Phase 13 已坐实），那边不能复用。

    ⚠️ **测试要换库时，patch 的是这个函数**：`app.db.session.new_session`。

    以前 `agent_service` / `rag_service` / `conversation_service` 三个模块各存一份
    `_session_factory`，靠 fixture 的 docstring 提醒"三个都要换" —— 而漏换的后果
    **不是测试变红，而是静默写进真 MySQL**。同一句警告要在两个 fixture 里各写一遍，
    本身就是"该合并"的信号；收敛成一处之后，"要换几个"这个问题不再存在。

    用法不变，仍是 `with new_session() as db:`（返回 session，不是上下文管理器）。
    """
    return SessionLocal()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用：请求开始建 session，结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
