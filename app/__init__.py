import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import check_unknown_env_keys, settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging


def _resolve_cors_origins() -> list[str]:
    """决定允许哪些来源跨域。

    优先级：显式配置的 CORS_ORIGINS > dev 环境放通全部 > 非 dev 不放通。
    判据用 APP_ENV（环境）而不是 DEBUG（日志开关），两者职责分开。
    """
    configured = settings.cors_origins_list
    if configured:
        return configured
    if settings.APP_ENV == "dev":
        return ["*"]
    return []


def create_app() -> FastAPI:
    """应用工厂：组装日志、中间件、异常处理、路由。"""
    setup_logging(settings.DEBUG)
    logger = logging.getLogger(__name__)

    # .env 里拼错的键会被静默忽略并回退默认值，启动时先喊一声
    unknown_keys = check_unknown_env_keys()
    if unknown_keys:
        logger.warning(
            ".env 存在未识别的配置项（拼写错误？已被忽略）: %s", ", ".join(unknown_keys)
        )

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=settings.DEBUG,
    )

    allowed_origins = _resolve_cors_origins()
    # 通配来源与 allow_credentials 按 CORS 规范不能共存：
    # 通配时关掉 credentials（前端用 Authorization 头传 JWT，本来也不需要 cookie）
    use_wildcard = "*" in allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=not use_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS 允许来源: %s", allowed_origins)

    register_exception_handlers(app)

    app.include_router(health.router, prefix=settings.API_PREFIX)

    return app
