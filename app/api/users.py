"""用户管理接口（仅系统管理员）。

最初只有 `GET /users` —— 唯一用途是让管理员拿到 user_id 去授权知识库成员
（没有它，知识库授权在真实前端里根本点不成）。现在补成一套完整的用户生命周期：

  · `GET   /users`               —— 用户列表（管理页要邮箱与注册时间）
  · `POST  /users`               —— **开通账号**（可指定初始角色）
  · `PATCH /users/{id}/role`     —— 改角色（**提权入口**，约束见 user_service）
  · `PATCH /users/{id}/status`   —— **停用 / 启用**（收回访问权里最重的一档）
  · `PATCH /users/{id}/password` —— **重置密码**（旧密码与已签发 token 一起失效）
  · `GET   /users/{id}/grants`   —— 这个人被授权了哪些库

⚠️ **刻意不提供"删除用户"**：删用户会牵连他的会话、消息、审计记录（外键级联），
而"停用"（`is_active=false`）能覆盖绝大多数真实需求且可回退 —— 停用之后
登录被拒、已签发的 token 也会立刻失效。删除是唯一不可逆的操作，
在一个可能只有单个管理员的系统里不值得开。

配套的是 `AUTH_ALLOW_REGISTER`（默认关）：账号主要由管理员在这里开，
自助注册那条路默认不开放。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import (
    PasswordResetRequest,
    RoleUpdateRequest,
    StatusUpdateRequest,
    UserBrief,
    UserCreateRequest,
    UserDetail,
)
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


@router.post("", summary="开通账号（仅系统管理员）")
def create_user(
    payload: UserCreateRequest,
    # 与改角色同档限流：也是敏感入口，正常没人会连点它
    _: None = Depends(rate_limit("chat")),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员开通账号，可**一开始就指定角色**。

    这是自助注册（默认已关）之外唯一的开号路径 —— 真实企业里账号本就该由管理员开。
    成功后返回完整用户信息（含邮箱与注册时间），管理页直接把它插进列表。
    """
    user = user_service.create_user(db, actor=admin, payload=payload)
    return success(
        UserDetail.model_validate(user).model_dump(mode="json"),
        f"已开通账号 {user.username}",
    )


@router.patch("/{user_id}/status", summary="停用 / 启用账号（仅系统管理员）")
def update_user_status(
    user_id: int,
    payload: StatusUpdateRequest,
    _: None = Depends(rate_limit("chat")),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """停用或启用账号。两条约束（不能停自己 / 不能让 admin 归零）在 `user_service.change_status`。

    返回体带 `changed`：重复提交同一个状态时它是 false，前端据此提示"未发生变化"。
    """
    data = user_service.change_status(
        db, actor=admin, target_id=user_id, is_active=payload.is_active
    )
    if not data["changed"]:
        return success(data, "状态未发生变化")
    return success(
        data, f"已{'启用' if payload.is_active else '停用'} {data['username']}"
    )


@router.patch("/{user_id}/password", summary="重置密码（仅系统管理员）")
def reset_user_password(
    user_id: int,
    payload: PasswordResetRequest,
    _: None = Depends(rate_limit("chat")),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员重置某人的密码。

    ⚠️ **重置之后该用户的登录态会全部失效** —— 旧密码用不了，已签发的 token
    也会因为密码指纹对不上而被拒（见 `security.password_fingerprint`）。
    这是刻意的：改密码最常见的动机就是"凭证可能泄露了"。
    响应里的 `is_self` 告诉前端"这次重置的是你自己"，好把当前会话也一起结束掉。
    """
    data = user_service.reset_password(
        db, actor=admin, target_id=user_id, new_password=payload.new_password
    )
    return success(
        data,
        f"已重置 {data['username']} 的密码"
        + ("（是你的账号，请用新密码重新登录）" if data["is_self"] else ""),
    )


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
