"""用户管理接口（仅系统管理员）。

最初只有 `GET /users` —— 唯一用途是让管理员拿到 user_id 去授权知识库成员
（没有它，知识库授权在真实前端里根本点不成）。现在补成一套完整的用户管理：

  · `GET   /users`              —— 用户列表（管理页要邮箱与注册时间）
  · `PATCH /users/{id}/role`    —— 改角色（**提权入口**，约束见 user_service）
  · `GET   /users/{id}/grants`  —— 这个人被授权了哪些库

⚠️ **刻意不提供"删除用户"**：删用户会牵连他的会话、消息、审计记录（外键级联），
而"禁用"（`is_active=false`）能覆盖绝大多数真实需求且可回退。删除是唯一
不可逆的操作，在一个可能只有单个管理员的系统里不值得开。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import RoleUpdateRequest, UserBrief, UserDetail
from app.services import user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("", summary="用户列表（仅系统管理员）")
def list_users(
    keyword: str | None = Query(default=None, min_length=1, max_length=64, description="按用户名/邮箱模糊查"),
    limit: int = Query(default=50, ge=1, le=200),
    full: bool = Query(
        default=False,
        description="true=含邮箱与注册时间（用户管理页用）；默认只给最小字段（选人授权用）",
    ),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """默认返回**最小字段**（id / username / is_active / role）—— 保持原样不动。

    管理页需要邮箱与注册时间时传 `full=true`。做成**显式开关**而不是直接放宽结构，
    是因为这个接口最早服务于"选人授权"，窄字段本身是一道守卫
    （`tests/test_users.py::test_admin_lists_users_without_secrets` 钉着它）：
    给授权下拉不需要邮箱，就不该顺手把它发出去。
    """
    users = user_service.list_users(db, keyword=keyword, limit=limit)
    schema = UserDetail if full else UserBrief
    return success([schema.model_validate(u).model_dump(mode="json") for u in users])


@router.patch("/{user_id}/role", summary="修改用户角色（仅系统管理员）")
def update_user_role(
    user_id: int,
    payload: RoleUpdateRequest,
    # 提权入口给最紧的一档限流：正常没人会连点它，撞上配额反而是个有价值的信号
    _: None = Depends(rate_limit("chat")),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """改角色。三条约束（不能改自己 / 不能没有 admin / 必须留审计）在 `user_service.change_role`。

    返回体带 `changed`：幂等提交（改成同一个角色）时它是 false，
    前端据此提示"未发生变化"，而不是把幂等当成失败。
    """
    data = user_service.change_role(
        db, actor=admin, target_id=user_id, role_code=payload.role_code
    )
    if not data["changed"]:
        return success(data, "角色未发生变化")
    return success(data, f"已把 {data['username']} 改为 {payload.role_code}")


@router.get("/{user_id}/grants", summary="查看某人被授权的知识库（仅系统管理员）")
def list_user_grants(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """从"人"这一侧看权限：他被授权了哪些库、各自什么权限。"""
    return success(user_service.list_grants(db, user_id))
