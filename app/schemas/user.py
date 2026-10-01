"""用户相关的响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
