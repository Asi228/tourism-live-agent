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
