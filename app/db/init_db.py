"""数据库初始化：建表 + 播种默认角色。

为什么要单独一个文件：
- 建表是"启动流程"的一部分，写在 main.py 里显式调用，测试时可以只调 create_app() 而不碰真库
- 角色是程序运行的前提数据（注册要按 code 找 employee），必须幂等播种
"""

import logging

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import Role  # noqa: F401  必须导入，模型才会注册到 Base.metadata
from app.repositories import role_repository

logger = logging.getLogger(__name__)

DEFAULT_ROLES = [
    {"code": "admin", "name": "管理员", "description": "系统管理员，可管理知识库与用户"},
    {"code": "employee", "name": "普通员工", "description": "普通员工，可访问被授权的知识库"},
]


def init_db() -> None:
    """幂等：重复执行不会重复建表，也不会重复插入角色。"""
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        created: list[str] = []
        for item in DEFAULT_ROLES:
            if role_repository.get_by_code(db, item["code"]) is None:
                db.add(Role(**item))
                created.append(item["code"])
        db.commit()

    logger.info("数据库初始化完成 | 新建角色: %s", created if created else "无（已存在）")
