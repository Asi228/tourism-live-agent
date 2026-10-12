"""用户相关的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    RegisterRequest,
)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class UserOut(BaseModel):
    """对外返回的用户信息 —— 故意不包含 password_hash。"""

    # from_attributes=True 允许直接从 SQLAlchemy 对象构造（不用手写 dict）
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime
    role: RoleOut


class UserBrief(BaseModel):
    """用户列表用的精简结构。

    故意不含 email，也不含 password_hash：这个接口的用途是让管理员拿到 user_id
    去授权知识库成员，不是把用户资料摊开给前端。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_active: bool
    role: RoleOut


class UserDetail(BaseModel):
    """用户管理页用的结构 —— 比 `UserBrief` 多 email 与注册时间。

    两个 schema 并存是刻意的：`UserBrief` 服务"选人授权"（越少字段越好），
    这里服务"管理用户"（要能看到邮箱和注册时间）。同一个人在不同场景下
    需要看到的字段不一样，硬合成一个只会让宽的那个到处传。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime
    role: RoleOut


class RoleUpdateRequest(BaseModel):
    """改角色的入参。只收 role_code —— 不用 role_id：code 是稳定契约，id 不是。"""

    role_code: str = Field(min_length=1, max_length=32, description="目标角色的 code")


class UserCreateRequest(RegisterRequest):
    """管理员开通账号的入参。

    字段校验**直接继承注册用的 `RegisterRequest`** —— username 的字符集、
    password 的长度上限（bcrypt 72 字节）这些规则必须两边一致，
    各写一份的话迟早出现"注册要求 6 位、管理员建号只要 3 位"的漂移。

    多的只有 `role_code`：自助注册写死 employee，管理员则可以一开始就指定角色
    （否则建完还得再调一次改角色接口）。默认仍是 employee —— 最小权限优先。
    """

    role_code: str = Field(
        default="employee", min_length=1, max_length=32, description="初始角色 code"
    )


class StatusUpdateRequest(BaseModel):
    """启用 / 停用的入参。布尔值直接表达目标状态，而不是让人传动作名。"""

    is_active: bool = Field(description="true=启用（可登录），false=停用")


class PasswordResetRequest(BaseModel):
    """管理员重置密码的入参。

    长度限制取的是与注册 / 开通**同一组常量**（`schemas.auth`）——
    密码规则的唯一定义在那里，别在这个文件里另写一份数字。
    """

    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="新密码（6-72 位）。重置后该用户的旧密码与已签发 token 一并失效",
    )

