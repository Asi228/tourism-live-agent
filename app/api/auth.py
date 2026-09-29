"""认证接口：注册 / 登录 / 当前用户。"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.user import UserOut
from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", summary="用户注册")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """注册成功直接返回用户信息（不含密码），不自动登录。"""
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
