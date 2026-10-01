"""数据库初始化：建表 + 播种默认角色 + 按需创建初始管理员。

为什么要单独一个文件：
- 建表是"启动流程"的一部分，写在 main.py 里显式调用，测试时可以只调 create_app() 而不碰真库
- 角色/管理员是程序运行的前提数据，必须幂等播种

为什么要有初始管理员：
注册接口默认给 employee 角色，而"创建知识库"要求系统 admin ——
没有播种机制的话系统里永远产生不了第一个管理员（鸡生蛋问题）。
密码从 .env 的 ADMIN_PASSWORD 读，**不硬编码默认密码**。
"""

import logging

from app.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
# 必须导入模型，它们才会注册到 Base.metadata（否则 create_all 建不出表）
from app.models import (  # noqa: F401
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeBaseMember,
    Role,
    User,
)
from app.repositories import document_repository, role_repository, user_repository

logger = logging.getLogger(__name__)

DEFAULT_ROLES = [
    {"code": "admin", "name": "管理员", "description": "系统管理员，可管理知识库与用户"},
    {"code": "employee", "name": "普通员工", "description": "普通员工，可访问被授权的知识库"},
]


def _seed_roles(db) -> list[str]:
    created: list[str] = []
    for item in DEFAULT_ROLES:
        if role_repository.get_by_code(db, item["code"]) is None:
            db.add(Role(**item))
            created.append(item["code"])
    db.commit()
    return created


def _ensure_admin(db) -> str | None:
    """按 .env 配置创建初始管理员。已存在则跳过。返回新建的用户名或 None。"""
    username = settings.ADMIN_USERNAME.strip()
    password = settings.ADMIN_PASSWORD

    if not username or not password:
        logger.warning(
            "未配置 ADMIN_USERNAME / ADMIN_PASSWORD，已跳过初始管理员创建。"
            "没有管理员账号将无法创建知识库。"
        )
        return None

    if user_repository.get_by_username(db, username) is not None:
        # 不静默跳过：改了 .env 里的 ADMIN_PASSWORD 却登不进去，最常见的原因就是这条
        logger.info(
            "管理员 %s 已存在，跳过创建（.env 里的 ADMIN_PASSWORD 不会更新已有账号的密码）",
            username,
        )
        return None

    role = role_repository.get_by_code(db, "admin")
    if role is None:
        logger.error("admin 角色缺失，无法创建初始管理员")
        return None

    user_repository.create(
        db,
        username=username,
        email=f"{username}@admin.local",
        password_hash=hash_password(password),
        role_id=role.id,
    )
    db.commit()
    logger.info("已创建初始管理员: %s", username)
    return username


def init_db() -> None:
    """幂等：重复执行不会重复建表、不会重复插角色、不会重复建管理员。"""
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        created_roles = _seed_roles(db)
        admin_username = _ensure_admin(db)
        stuck = _count_stuck_documents(db)

    logger.info(
        "数据库初始化完成 | 新建角色: %s | 管理员: %s",
        created_roles if created_roles else "无（已存在）",
        admin_username or "无新建",
    )
    if stuck:
        # 进程被强杀时，后台解析任务会连同进程一起消失，
        # 文档就永远停在 pending / processing。这里至少让它被看见。
        logger.warning(
            "有 %s 份文档未处理完成（进程中断导致），可重新上传或等待后续阶段提供重试入口",
            stuck,
        )


def _count_stuck_documents(db) -> int:
    """统计卡在 pending / processing 的文档数量。"""
    return len(document_repository.list_unfinished(db))
