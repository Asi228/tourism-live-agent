"""认证业务逻辑：注册、登录、按 id 取用户。

这一层负责"业务规则"（用户名唯一、密码对不对、账号是否被禁），
数据库读写全部交给 Repository，不在这里写 SQL。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories import role_repository, user_repository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse

logger = logging.getLogger(__name__)

# 注册时的默认角色
DEFAULT_ROLE_CODE = "employee"


def register(db: Session, payload: RegisterRequest) -> User:
    """注册新用户，默认角色 employee。"""
    if user_repository.get_by_username(db, payload.username) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "用户名已被占用")
    if user_repository.get_by_email(db, payload.email) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "邮箱已被注册")

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
        extra={"username": user.username, "role": user.role.code},
    )
    logger.info("用户登录 user_id=%s username=%s role=%s", user.id, user.username, user.role.code)

    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """给 get_current_user 依赖用。"""
    return user_repository.get_by_id(db, user_id)
