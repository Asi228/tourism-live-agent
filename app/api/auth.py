"""认证接口：注册 / 登录 / 当前用户 / 公开配置。"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import AuthConfigOut, LoginRequest, RegisterRequest
from app.schemas.user import UserOut
from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/config", response_model=ApiResponse[AuthConfigOut], summary="公开的认证配置")
def auth_config():
    """登录页需要知道的事（**无需登录**）。

    目前只有一项：自助注册开没开 —— 据此决定显示还是隐藏"注册"入口。
    不放这个接口的话，前端只能"先显示、点了再报错"，那是一种更差的体验。

    ⚠️ 这里只放**不含敏感信息**的开关。别顺手把 JWT 有效期、管理员用户名
    之类的塞进来 —— 这是个匿名可访问的端点。
    """
    return success({"allow_register": settings.AUTH_ALLOW_REGISTER})


@router.post("/register", summary="用户注册")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """注册成功直接返回用户信息（不含密码），不自动登录。

    ⚠️ 受 `AUTH_ALLOW_REGISTER` 门控（默认关，见 config）。关着的时候返回
    40300 —— 前端应当在"注册"入口上就拦住（靠 `GET /auth/config`），
    这里是最后一道。
    """
    user = auth_service.register(db, payload)
    return success(UserOut.model_validate(user).model_dump(mode="json"), "注册成功")


@router.post("/login", summary="用户登录")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """登录成功返回 JWT，前端后续请求把它放进 Authorization: Bearer <token>。"""
    token = auth_service.login(db, payload)
    return success(token.model_dump(), "登录成功")


@router.get("/me", summary="获取当前登录用户")
def me(current_user: User = Depends(get_current_user)):
    """需要带上有效 token 才能访问。"""
    return success(UserOut.model_validate(current_user).model_dump(mode="json"))
