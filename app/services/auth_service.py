"""认证业务逻辑：注册、登录、按 id 取用户。

这一层负责"业务规则"（用户名唯一、密码对不对、账号是否被禁），
数据库读写全部交给 Repository，不在这里写 SQL。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.core.security import (
    create_access_token,
    hash_password,
    password_fingerprint,
    verify_password,
)
from app.models.user import User
from app.repositories import role_repository, user_repository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.services import user_service

logger = logging.getLogger(__name__)

# 注册时的默认角色
DEFAULT_ROLE_CODE = "employee"


def register(db: Session, payload: RegisterRequest) -> User:
    """自助注册新用户，默认角色 employee。

    ⚠️ 受 `AUTH_ALLOW_REGISTER` 门控（**默认关**）。企业内部的账号应当由
    管理员开通（`user_service.create_user`）—— 把注册页挂在公网上等于给
    "谁能进这个系统"开了一条不需要许可的入口，还会被人用来占用户名与邮箱。

    关闭时给 40300 而不是 40400：接口是存在的、只是被策略关掉了，
    "403 告诉你这件事被禁止"比"404 让你以为路径写错了"更有用。
    """
    if not settings.AUTH_ALLOW_REGISTER:
        raise AppException(
            ErrorCode.FORBIDDEN, "本系统未开放自助注册，账号请联系管理员开通"
        )

    # 唯一性检查与"管理员开通"共用一份（规则必须两边一致）
    user_service.ensure_unique(db, username=payload.username, email=payload.email)

    role = role_repository.get_by_code(db, DEFAULT_ROLE_CODE)
    if role is None:
        # 正常不会走到这里：init_db() 启动时已经播种好角色
        raise AppException(ErrorCode.DB_ERROR, "默认角色缺失，请先执行数据库初始化")

    user = user_repository.create(
        db,
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    db.commit()  # 事务边界在这一层：本业务单元的所有写到这里才一起生效
    logger.info("新用户注册 user_id=%s username=%s", user.id, user.username)
    return user


def login(db: Session, payload: LoginRequest) -> TokenResponse:
    """登录成功返回 JWT。"""
    user = user_repository.get_by_username(db, payload.username)

    # 故意不区分"用户不存在"和"密码错误"：否则可以被用来枚举系统里有哪些账号
    if user is None or not verify_password(payload.password, user.password_hash):
        logger.warning("登录失败 username=%s", payload.username)
        raise AppException(ErrorCode.UNAUTHORIZED, "用户名或密码错误")

    if not user.is_active:
        raise AppException(ErrorCode.FORBIDDEN, "账号已被禁用")

    token = create_access_token(
        user.id,
        extra={
            "username": user.username,
            "role": user.role.code,
            # 密码指纹：密码一被改（管理员重置 / 将来可能的自助改密），
            # 这个值就对不上 → 旧 token 立即失效。见 security.password_fingerprint。
            "pwd": password_fingerprint(user.password_hash),
        },
    )
    logger.info("用户登录 user_id=%s username=%s role=%s", user.id, user.username, user.role.code)

    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """给 get_current_user 依赖用。"""
    return user_repository.get_by_id(db, user_id)
