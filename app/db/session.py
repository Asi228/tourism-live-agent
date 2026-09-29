from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.mysql_url,
    pool_pre_ping=True,                      # 取连接前先探活，避免 MySQL 断连后报 Lost connection
    pool_recycle=3600,                       # 1 小时回收连接，防 MySQL 默认 8h 超时
    pool_size=settings.DB_POOL_SIZE,         # 常驻连接数
    max_overflow=settings.DB_MAX_OVERFLOW,   # 峰值允许临时超出的连接数
    echo=settings.SQL_ECHO,                  # 独立开关，不跟 DEBUG 绑，避免控制台刷满 SQL
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用：请求开始建 session，结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
