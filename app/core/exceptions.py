import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import ErrorCode, error

logger = logging.getLogger(__name__)

# HTTP 状态码 -> 业务错误码。显式列出来，不搞隐式算术，
# 免得出现 "500 -> 50000 但 ErrorCode 里根本没定义" 这类情况。
_HTTP_STATUS_TO_ERROR_CODE: dict[int, int] = {
    400: ErrorCode.PARAM_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    422: ErrorCode.PARAM_ERROR,
    500: ErrorCode.SERVER_ERROR,
}


def _map_http_status(status_code: int) -> int:
    """把 HTTP 状态码映射成业务错误码。

    表里没有的 4xx 按 status*100 兜底；5xx 和更奇怪的都归 UNKNOWN。
    """
    if status_code in _HTTP_STATUS_TO_ERROR_CODE:
        return _HTTP_STATUS_TO_ERROR_CODE[status_code]
    if 400 <= status_code < 500:
        return status_code * 100
    return ErrorCode.UNKNOWN


class AppException(Exception):
    """业务异常基类：抛出它，全局处理器统一转成标准响应。"""

    def __init__(self, code: int = ErrorCode.UNKNOWN, message: str = "服务异常"):
        self.code = code
        self.message = message
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常收敛成统一响应格式。"""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        logger.warning("业务异常 path=%s code=%s msg=%s", request.url.path, exc.code, exc.message)
        return JSONResponse(status_code=200, content=error(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # 参数校验失败：把 Pydantic 的报错压成一行方便看
        detail = "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg', '')}"
            for e in exc.errors()
        )
        logger.warning("参数异常 path=%s detail=%s", request.url.path, detail)
        return JSONResponse(status_code=200, content=error(ErrorCode.PARAM_ERROR, detail))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        code = _map_http_status(exc.status_code)
        logger.warning("HTTP异常 path=%s status=%s code=%s", request.url.path, exc.status_code, code)
        return JSONResponse(status_code=200, content=error(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def global_handler(request: Request, exc: Exception):
        logger.exception("未捕获异常 path=%s", request.url.path)
        return JSONResponse(status_code=200, content=error(ErrorCode.UNKNOWN, "服务器内部错误"))
