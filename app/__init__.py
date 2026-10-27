import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    agent,
    audit,
    auth,
    chat,
    chunks,
    conversations,
    documents,
    faq,
    feedback,
    health,
    knowledge,
    live_commerce,
    memories,
    mock_platform,
    rpa,
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
FRONTEND_DIST_DIR = Path(BASE_DIR) / "frontend" / "dist"

_DEMO_ALLOWED_WRITES = (
    "/api/chat",
    "/api/agent/chat",
    "/api/search",
    "/api/feedback",
)

_DEMO_SANDBOX_WRITE_ROUTES = (
    ("POST", re.compile(r"^/api/commerce/sessions$")),
    ("POST", re.compile(r"^/api/commerce/sessions/\d+/(?:items|packages|inspections)$")),
    ("POST", re.compile(r"^/api/commerce/price-preview$")),
    ("PUT", re.compile(r"^/api/rpa/monitoring-plans/\d+$")),
    ("POST", re.compile(r"^/api/rpa/tasks$")),
    ("POST", re.compile(r"^/api/rpa/tasks/\d+/requeue$")),
    ("POST", re.compile(r"^/api/rpa/approvals$")),
    ("POST", re.compile(r"^/api/rpa/approvals/\d+/decision$")),
    ("POST", re.compile(r"^/api/agent/live-sessions/\d+/(?:explain|approval-draft)$")),
    ("POST", re.compile(r"^/api/memories$")),
    ("PATCH", re.compile(r"^/api/memories/\d+$")),
    ("DELETE", re.compile(r"^/api/memories/\d+$")),
    ("DELETE", re.compile(r"^/api/conversations/\d+$")),
)


def _demo_sandbox_write_allowed(method: str, path: str) -> bool:
    if path.startswith(_DEMO_ALLOWED_WRITES):
        return True
    return any(
        method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in _DEMO_SANDBOX_WRITE_ROUTES
    )


def _install_demo_read_only_guard(app: FastAPI) -> None:
    """公网展示仅开放业务沙箱写操作，保护账号、权限和基础知识数据。"""
    if not settings.DEMO_READ_ONLY:
        return

    @app.middleware("http")
    async def guard_demo_mutations(request: Request, call_next):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        path = request.url.path
        if path == f"{settings.API_PREFIX}/auth/login" or _demo_sandbox_write_allowed(
            request.method, path
        ):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            try:
                from app.core.security import decode_access_token

                payload = decode_access_token(authorization.split(" ", 1)[1])
                if payload.get("username") == settings.DEMO_READ_ONLY_USERNAME:
                    return JSONResponse(
                        status_code=200,
                        content={
                            "code": 40300,
                            "message": "互动演示仅开放直播运营、审批、自动化和知识问答；账号、权限与基础资料受保护",
                            "data": None,
                        },
                    )
            except Exception:  # 令牌是否合法仍交给正常鉴权链路处理
                pass
        return await call_next(request)


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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动时把向量集合"拉起来"一次 —— 这是"提问查不到、重灌向量才好"那起现象的自愈入口。

    为什么在启动做、而不是塞进检索路径：`load_collection` 幂等但是一次网络往返，
    放进每次检索会让正常路径白白变慢；而它要修的恰恰就是"服务刚起来那一下读不到数据"
    这个时刻 —— 启动时补一次，成本一次，覆盖整段时间窗口。

    刻意**不阻断启动**：Milvus 挂的时候应用仍要能起来（登录、用户、知识库/文档的 CRUD
    都不依赖它），所以这里只留一条 warning。代价说清楚：Milvus 不可达时启动会多等
    最多 `MILVUS_TIMEOUT` 秒（默认 10s）—— 这是"能自愈"换来的，如果哪天嫌慢，
    该改的是把探测换成 `vector_store.probe()` 那条短超时路径，而不是删掉这次加载。

    顺带把**关键词路**也预热一次（jieba 词典构建，实测 ~0.8s）——
    不预热它会落在每个新进程的第一个提问上，详见 `app/rag/keyword.py` 的 `warmup()`。
    """
    log = logging.getLogger(__name__)
    from app.rag import keyword, vector_store

    try:
        if not vector_store.ensure_loaded():
            log.warning("启动时向量集合未加载完成：检索可能读不到已存在的向量（见「知识库」页的向量库自检）")
    except Exception as exc:  # noqa: BLE001
        # get_client() 连不上时会抛 AppException —— 那不能让服务起不来
        log.warning("启动时加载向量集合失败（不阻断启动）：%s", str(exc)[:150])

    # 关键词路预热：不预热的话这笔成本落在**每个新进程的第一个提问**上；
    # 提前到这里，用户侧就没有"第一问特别慢"的窗口。同样**不阻断启动** ——
    # 失败只是退回"首问付一次"的原状，功能不受影响。
    if settings.RAG_HYBRID_ENABLED:
        try:
            keyword.warmup()
        except Exception as exc:  # noqa: BLE001
            log.warning("启动时 jieba 预热失败（不阻断启动）：%s", str(exc)[:150])
    yield


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
        lifespan=_lifespan,
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
    _install_demo_read_only_guard(app)

    app.include_router(health.router, prefix=settings.API_PREFIX)
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    app.include_router(users.router, prefix=settings.API_PREFIX)
    app.include_router(knowledge.router, prefix=settings.API_PREFIX)
    app.include_router(live_commerce.router, prefix=settings.API_PREFIX)
    app.include_router(documents.router, prefix=settings.API_PREFIX)
    app.include_router(search.router, prefix=settings.API_PREFIX)
    app.include_router(vector.router, prefix=settings.API_PREFIX)
    app.include_router(chat.router, prefix=settings.API_PREFIX)
    app.include_router(agent.router, prefix=settings.API_PREFIX)
    app.include_router(conversations.router, prefix=settings.API_PREFIX)
    app.include_router(memories.router, prefix=settings.API_PREFIX)
    app.include_router(feedback.router, prefix=settings.API_PREFIX)
    app.include_router(faq.router, prefix=settings.API_PREFIX)
    app.include_router(chunks.router, prefix=settings.API_PREFIX)
    app.include_router(stats.router, prefix=settings.API_PREFIX)
    app.include_router(audit.router, prefix=settings.API_PREFIX)
    app.include_router(rpa.router, prefix=settings.API_PREFIX)
    # ⚠️ 模拟电商后台**不带 API 前缀**：它模拟的是"外部平台网页"（HTML + 表单），
    # 不是本系统的 JSON API —— RPA 用浏览器访问它，路径要与真实平台形态一致。
    app.include_router(mock_platform.router)

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

    # 生产容器同时托管 Vue 构建产物，API/模拟平台路由已先注册，因此不会被静态站覆盖。
    if settings.APP_ENV == "prod" and FRONTEND_DIST_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
        logger.info("前端构建产物已挂载: /")

    return app
