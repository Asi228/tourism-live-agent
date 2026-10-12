"""认证相关的请求/响应模型。

这些校验在进入业务代码之前就完成了：格式不对直接由全局异常处理器
返回 code=40000，Service 层不用重复校验。
"""

from pydantic import BaseModel, EmailStr, Field

# 密码长度限制 —— **注册、管理员开通、重置密码三处共用同一组数字**。
# 散成三份的话，哪天想把下限提到 8 位，很容易只改一处，
# 于是出现"注册要求 6 位、重置却允许 3 位"这种不一致。
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 72  # bcrypt 算法上限（72 字节）


class RegisterRequest(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_]+$",
        description="3-64 位，仅允许字母、数字、下划线",
    )
    email: EmailStr = Field(description="邮箱（pydantic 会校验格式）")
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="6-72 位（bcrypt 算法上限 72 字节）",
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="有效期，单位秒")
