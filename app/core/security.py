"""密码哈希与 JWT 签发/校验。

密码哈希用 bcrypt 官方库，不用 passlib：
passlib 1.7.4（2020 年最后发版）与 bcrypt 4.1+ 存在已知不兼容（读 bcrypt.__about__ 报错），
直接用 bcrypt 只有三行代码，反而更省事、更没有版本坑。
"""

import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt 算法本身只取前 72 字节，超长输入会抛错，这里统一截断
BCRYPT_MAX_BYTES = 72


def hash_password(plain_password: str) -> str:
    """把明文密码转成 bcrypt 哈希。

    gensalt() 每次生成不同的随机盐，所以同一个密码两次哈希结果不同。
    """
    raw = plain_password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文密码与哈希是否匹配。任何异常一律视为不匹配。"""
    try:
        raw = plain_password.encode("utf-8")[:BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_fingerprint(password_hash: str) -> str:
    """密码哈希的短指纹，用来让"改密码"能立刻作废已签发的 token。

    为什么需要它：JWT 是无状态的，服务端**不吊销**已发出的 token。于是有个洞 ——
    改密码最常见的动机恰恰是"我的凭证可能泄露了"，而旧 token 还能用到过期
    （默认 24 小时），**改密码就等于没改**。

    做法：签发时把当前密码哈希的指纹塞进 token，鉴权时比对。密码一改，
    bcrypt 的哈希就变了（加随机盐，每次哈希都不同）→ 指纹不匹配 → 旧 token 立即失效。

    比"加一个 `token_version` / `password_changed_at` 字段"好在**不用迁移、不用回库读额外列**：
    bcrypt 哈希本来就在 `User` 上，鉴权时已经取到了。

    ⚠️ 指纹可公开：它取自 bcrypt 哈希（本身就是单向、抗反推的），
    算出来也**推不出密码**，泄露 token 不比泄露哈希更糟。取 16 位十六进制足够区分。

    ⚠️ 副作用（是特性不是 bug）：**任何**改变 `password_hash` 的路径都会自动让旧 token 失效，
    包括将来可能加的"用户自助改密"，不必记得在每处手动清理。
    """
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def create_access_token(user_id: int, extra: dict | None = None) -> str:
    """签发 JWT。

    sub 用字符串（PEP 规范要求 claim 是字符串），额外带上 username / role 方便日志排查。
    真正判权限时不依赖 payload 里的 role，一定回数据库查（防止角色变更后旧 token 还生效）。
    """
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解码并校验 JWT。签名不对/已过期会抛 jwt.PyJWTError 的子类异常。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
