"""用户相关的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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

