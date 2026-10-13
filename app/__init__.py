import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    agent,
    audit,
    auth,
    chat,
    conversations,
    documents,
    feedback,
    health,
    knowledge,
    memories,
    search,
    stats,
    users,
    vector,
)
from app.config import BASE_DIR, check_unknown_env_keys, settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging

# 开发期自用的接口调试台（纯静态单文件，不属于业务代码）
DEVTOOLS_DIR = Path(BASE_DIR) / "devtools"


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

    # 恒传 False：Starlette 的 ServerErrorMiddleware 在 debug=True 时会跳过我们注册的
    # Exception 处理器，直接把原始 traceback 返给客户端（HTTP 500 + 泄漏 SQL 参数/绝对路径），
    # 同时绕过日志脱敏。完整堆栈由下面的 logger.exception 写进 app.log，开发时看日志即可。
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=False,
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
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    app.include_router(users.router, prefix=settings.API_PREFIX)
    app.include_router(knowledge.router, prefix=settings.API_PREFIX)
    app.include_router(documents.router, prefix=settings.API_PREFIX)
    app.include_router(search.router, prefix=settings.API_PREFIX)
    app.include_router(vector.router, prefix=settings.API_PREFIX)
    app.include_router(chat.router, prefix=settings.API_PREFIX)
    app.include_router(agent.router, prefix=settings.API_PREFIX)
    app.include_router(conversations.router, prefix=settings.API_PREFIX)
    app.include_router(memories.router, prefix=settings.API_PREFIX)
    app.include_router(feedback.router, prefix=settings.API_PREFIX)
    app.include_router(stats.router, prefix=settings.API_PREFIX)
    app.include_router(audit.router, prefix=settings.API_PREFIX)

    # 调试台只在 dev 环境挂载：生产环境（APP_ENV=prod）压根不存在这个路径，
    # 外部访问不到。安全靠"默认不挂载"，而不是靠人记得删文件。
    if settings.APP_ENV == "dev":
        if DEVTOOLS_DIR.exists():
            app.mount(
                "/devtools",
                StaticFiles(directory=str(DEVTOOLS_DIR), html=True),
                name="devtools",
            )
            # 只打印路径，不打印端口：实际监听端口由 uvicorn 命令行（--port）决定，
            # settings.APP_PORT 未必等于它，写死会给出错误的访问地址。
            logger.info("调试台已挂载: /devtools/（仅本机开发用）")
        else:
            # 显式记一条，避免"APP_ENV=dev 但页面打不开"时毫无线索
            logger.warning("APP_ENV=dev 但未找到调试台目录 %s，已跳过挂载", DEVTOOLS_DIR)
    else:
        logger.info("非 dev 环境（APP_ENV=%s），调试台未挂载", settings.APP_ENV)

    return app
