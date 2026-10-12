"""FastAPI 依赖注入器。

放在 api/ 而不是 core/：core/ 只放与业务无关的基础设施，
而这里要查数据库、要调 Service，属于接口层的职责。
"""

import logging

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.core.security import decode_access_token, password_fingerprint
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service

logger = logging.getLogger(__name__)

# auto_error=False：自己抛统一格式的 AppException，而不是让 FastAPI 抛默认 403
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 里解析出当前用户。

    任何一步失败都抛 AppException(40100)，前端只需处理一种错误格式。
    """
    if credentials is None or not credentials.credentials:
        raise AppException(ErrorCode.UNAUTHORIZED, "未提供认证信息")

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise AppException(ErrorCode.UNAUTHORIZED, "登录已过期，请重新登录")
    except jwt.PyJWTError as e:
        logger.warning("JWT 校验失败: %s", e)
        raise AppException(ErrorCode.UNAUTHORIZED, "认证信息无效")

    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise AppException(ErrorCode.UNAUTHORIZED, "认证信息无效")

    # 权限一定回数据库查，不信任 token 里带的 role —— 角色被改后旧 token 立即失效
    user = auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise AppException(ErrorCode.UNAUTHORIZED, "用户不存在")
    if not user.is_active:
        raise AppException(ErrorCode.FORBIDDEN, "账号已被禁用")

    # 密码指纹比对 —— 密码被改过（管理员重置）时，旧 token 立即作废。
    # 没有这一步，"改密码"就只是"下次登录要用新密码"，而手上那张票还能用到过期：
    # 改密码最常见的动机恰恰是"凭证可能泄露了"。
    # ⚠️ **严格口径**：不带这个 claim 的 token 一并拒掉（本项目还没上线，
    # 宽松放行会留下"老 token 永久有效"的洞）。
    if payload.get("pwd") != password_fingerprint(user.password_hash):
        logger.info("token 的密码指纹已过期 user_id=%s（密码被改过）", user.id)
        raise AppException(ErrorCode.UNAUTHORIZED, "登录状态已失效，请重新登录")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求管理员角色。用法：def api(admin: User = Depends(require_admin))"""
    if current_user.role.code != "admin":
        logger.warning("越权访问 user_id=%s role=%s", current_user.id, current_user.role.code)
        raise AppException(ErrorCode.FORBIDDEN, "需要管理员权限")
    return current_user


def rate_limit(kind: str):
    """限流依赖工厂，按"账号 + 用途"计。用法：

        def api(_: None = Depends(rate_limit("chat")), ...):

    放在依赖层而不是中间件，是因为配额要按**接口的贵贱**分档
    （chat 一次打两个付费上游、vectorize 一次可能打几百条 embedding），
    中间件只能按路径前缀拍脑袋。

    实现是**基于 Redis 的跨进程固定窗口**（`--workers N` 也准），详见 app/core/rate_limit_redis.py。
    那里同时写明了"Redis 挂了就放行"这个降级选择与它的代价。
    """
    from app.core.rate_limit_redis import limiter  # 局部 import：避免 core→api 的反向依赖

    def _dep(current_user: User = Depends(get_current_user)) -> None:
        limiter.check(kind, current_user.id)

    return _dep
