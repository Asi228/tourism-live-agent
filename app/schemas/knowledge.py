"""知识库与成员的请求 / 响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 用 Literal 而不是普通 str：权限值写错在参数校验阶段就被拦下（code=40000），
# 不会变成一条脏数据进数据库
Permission = Literal["read", "write", "admin"]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="知识库名称，全局唯一")
    description: str | None = Field(default=None, max_length=512)
    is_public: bool = Field(default=False, description="True=所有登录用户可读")


class KnowledgeBaseUpdate(BaseModel):
    """全部字段可选，只改传上来的那几项。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_public: bool | None = None


class KbOwnerOut(BaseModel):
    """列表/详情里展示创建者，前端不用再拿 owner_id 去查一次用户。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class KnowledgeBaseOut(BaseModel):
    # from_attributes 允许直接从 SQLAlchemy 对象构造
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    owner_id: int
    owner: KbOwnerOut
    is_public: bool
    created_at: datetime
    updated_at: datetime
    # 当前登录用户对这个库的权限，方便前端决定按钮显隐（不是数据库字段）
    my_permission: str | None = None


class MemberAdd(BaseModel):
    user_id: int = Field(description="要授权的用户 id")
    permission: Permission = "read"


class MemberOut(BaseModel):
    """成员信息（带上用户名，前端不用再查一次）。"""

    id: int
    user_id: int
    username: str
    permission: str
    created_at: datetime
