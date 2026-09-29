"""认证相关的请求/响应模型。

这些校验在进入业务代码之前就完成了：格式不对直接由全局异常处理器
返回 code=40000，Service 层不用重复校验。
"""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_]+$",
        description="3-64 位，仅允许字母、数字、下划线",
    )
    email: EmailStr = Field(description="邮箱（pydantic 会校验格式）")
    password: str = Field(
        min_length=6,
        max_length=72,
        description="6-72 位（bcrypt 算法上限 72 字节）",
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="有效期，单位秒")
