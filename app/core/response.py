from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应结构（主要用于 Swagger 文档展示）。"""

    code: int = 0
    message: str = "success"
    data: T | None = None


def success(data: Any = None, message: str = "success") -> dict:
    """成功响应。"""
    return {"code": 0, "message": message, "data": data}


def error(code: int, message: str, data: Any = None) -> dict:
    """失败响应。"""
    return {"code": code, "message": message, "data": data}


class ErrorCode:
    """错误码约定，按区间分段，后续阶段在区间内扩展。

    分段规则：
      4xxxx —— 客户端问题，编号 = HTTP 状态码 * 100
      5xxxx —— 服务端问题，5000x 按依赖组件细分
    """

    # ===== 4xxxx：客户端 =====
    PARAM_ERROR = 40000          # 对应 HTTP 400 参数错误
    UNAUTHORIZED = 40100         # 对应 HTTP 401 未认证
    FORBIDDEN = 40300            # 对应 HTTP 403 无权限
    NOT_FOUND = 40400            # 对应 HTTP 404 资源不存在
    METHOD_NOT_ALLOWED = 40500   # 对应 HTTP 405 方法不允许
    TOO_MANY_REQUESTS = 42900    # 对应 HTTP 429 请求过于频繁（限流）

    # ===== 5xxxx：服务端 =====
    SERVER_ERROR = 50000         # 对应 HTTP 500 服务端通用错误
    FILE_ERROR = 50001           # 文件异常
    DB_ERROR = 50002             # 数据库异常
    REDIS_ERROR = 50003          # Redis 异常
    MILVUS_ERROR = 50004         # Milvus 异常
    LLM_ERROR = 50005            # LLM 异常
    TOOL_ERROR = 50006           # Tool 异常
    # 硅基流动侧的调用失败：Embedding（BGE-M3）**与 Rerank（BGE-Reranker）共用这一个码**
    # —— 它们同一家服务商、同一个 key，拆成两个码反而让排障更绕。
    # 看到 50007 就去查 EMBEDDING_API_KEY / EMBEDDING_BASE_URL 那一套配置。
    EMBEDDING_ERROR = 50007      # 硅基流动侧调用失败（embedding / rerank）
    UNKNOWN = 59999              # 未知异常兜底

    # 说明：它和 LLM 分开，是因为两边是**两家服务商、两套 key**
    # （DeepSeek 提供对话，硅基流动提供 BGE-M3 / BGE-Reranker）。
    # 只给一个 50005 的话，看错误码分不清该去查哪个 key。
    # 这也符合本段"按依赖组件细分"的既有规则（文件/DB/Redis/Milvus/LLM/Tool）。
