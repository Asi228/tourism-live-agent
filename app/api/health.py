import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.core.response import ErrorCode, error, success
from app.db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
def health():
    """健康检查：顺便探一次 MySQL 连通性。

    HTTP 状态码仍统一 200（项目约定），但 DB 不通时 code 回 50002，
    监控 / 探活脚本靠 body 里的 code 就能判定，不会把故障误判成健康。
    """
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        # 真因（Access denied / can't connect）完整记日志，脱敏由日志层负责
        logger.warning("MySQL 连通性检查失败: %s", e)

    payload = {"api": "ok", "mysql": "ok" if db_ok else "fail"}
    if not db_ok:
        # 对外只给一句话，不把主机名、用户名等内部信息带进响应体
        return error(ErrorCode.DB_ERROR, "MySQL 不可用", payload)
    return success(payload)
