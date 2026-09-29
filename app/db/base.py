from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的父类。Phase 2 的 Role/User 会继承它。"""

    pass
