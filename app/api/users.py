"""用户查询接口：给管理员定位 user_id 用。

为什么需要它：授权知识库成员必须传 user_id，而此前系统里只有 /auth/me
能拿到"自己"的 id —— 没有这个接口，知识库授权在真实前端里根本点不成。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserBrief
from app.services import user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("", summary="用户列表（仅系统管理员）")
def list_users(
    keyword: str | None = Query(default=None, min_length=1, max_length=64, description="按用户名/邮箱模糊查"),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = user_service.list_users(db, keyword=keyword, limit=limit)
    return success([UserBrief.model_validate(u).model_dump(mode="json") for u in users])
