"""文本向量化 —— LangChain 封装 + 硅基流动 BGE-M3。

为什么用 LangChain 的 OpenAIEmbeddings 接硅基流动：
硅基流动提供的是 **OpenAI 兼容协议**（`/v1/embeddings`），
所以直接用 `OpenAIEmbeddings` 改 base_url 即可，不用自己拼 HTTP 请求。

⚠️ 必须设 `check_embedding_ctx_length=False`（本文件已设）：
它的默认值是 True，会拿 **tiktoken 按 OpenAI 的 tokenizer** 去预处理文本。
硅基流动不是 OpenAI，两边的分词规则不同，开着会出现"文本被错误切短"甚至直接报错。
这是"用 OpenAI 兼容协议接第三方服务"的经典坑。

批处理策略（实测数据，见 2026-10-02 工作记录）：
  1 条 ≈ 950 ms    32 条 ≈ 2373 ms    64 条 ≈ 1713 ms    200 条 ≈ 2664 ms
延迟不随条数线性增长 → 必须批量送。100 个 chunk：批送约 2 秒，逐条送约 100 秒。
"""

import logging

from langchain_openai import OpenAIEmbeddings

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

_client: OpenAIEmbeddings | None = None


def _get_client() -> OpenAIEmbeddings:
    """惰性单例：只有真正要向量化时才创建，避免没配 key 就启动失败。"""
    global _client
    if _client is None:
        if not settings.EMBEDDING_API_KEY:
            raise AppException(ErrorCode.EMBEDDING_ERROR, "未配置 EMBEDDING_API_KEY")
        if not settings.EMBEDDING_BASE_URL:
            raise AppException(ErrorCode.EMBEDDING_ERROR, "未配置 EMBEDDING_BASE_URL")

        _client = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
            check_embedding_ctx_length=False,  # 见本文件顶部说明，不能删
        )
        logger.info(
            "Embedding 客户端就绪 model=%s base_url=%s batch=%s",
            settings.EMBEDDING_MODEL, settings.EMBEDDING_BASE_URL,
            settings.EMBEDDING_BATCH_SIZE,
        )
    return _client


def reset_client() -> None:
    """丢掉缓存的客户端。

    给"配置变了要重建客户端"的场景用（测试里改完 Settings 调一次）。
    """
    global _client
    _client = None


def _check_dimension(vector: list[float]) -> list[float]:
    """维度必须和配置一致。

    不校验的话，将来换了模型（比如 768 维）而 Milvus collection 是按 1024 建的，
    插入时才会报一个很难懂的错。这里提前拦住，错误信息直接说明问题。
    """
    if len(vector) != settings.EMBEDDING_DIM:
        raise AppException(
            ErrorCode.EMBEDDING_ERROR,
            f"向量维度不符：期望 {settings.EMBEDDING_DIM}，实际 {len(vector)}"
            f"（模型 {settings.EMBEDDING_MODEL} 与 EMBEDDING_DIM 配置不一致？）",
        )
    return vector


def _wrap_error(exc: Exception) -> AppException:
    """把第三方异常转成我们自己的错误码。

    注意：上游返回的是 **HTTP 400 + body 里的 code**（实测 20012 模型不存在 /
    20015 参数非法），和我们"HTTP 恒 200、真实码在 body"的约定相反，
    所以这里统一收敛成 AppException，不让上游格式漏到我们的 API 层。

    上游原文**只进日志，不进响应**：它可能带 base_url、模型名、请求参数，
    属于内部细节。响应统一给一句话，与 api/health.py 的口径一致。
    另外日志里也绝不带 api key。
    """
    logger.warning("Embedding 调用失败: %s", str(exc)[:300])
    return AppException(
        ErrorCode.EMBEDDING_ERROR, "向量化服务调用失败，请稍后重试"
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把一批文本向量化，返回与输入**等长同序**的向量列表。

    输入里的空白文本会被替换成一个占位串而不是丢弃 ——
    因为调用方（向量入库）是靠下标和 chunk 一一对应的，
    这里少返回一个，后面就会整体错位。返回等长是最安全的契约。
    """
    if not texts:
        return []

    # 空串实测不报错，会返回一个"合法但无意义"的向量，所以由我们主动挡掉
    safe = [(t if t and t.strip() else "\u200b") for t in texts]

    client = _get_client()
    batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
    vectors: list[list[float]] = []

    for start in range(0, len(safe), batch_size):
        batch = safe[start:start + batch_size]
        try:
            got = client.embed_documents(batch)
        except Exception as exc:  # noqa: BLE001 — 上游异常类型不稳定，统一包装
            raise _wrap_error(exc) from exc

        if len(got) != len(batch):
            raise AppException(
                ErrorCode.EMBEDDING_ERROR,
                f"向量化返回条数不符：送入 {len(batch)} 条，返回 {len(got)} 条",
            )
        vectors.extend(_check_dimension(v) for v in got)

    logger.info("向量化完成 texts=%s dim=%s", len(texts), settings.EMBEDDING_DIM)
    return vectors


def embed_query(text: str) -> list[float]:
    """把一句查询向量化。

    和 embed_texts 走同一套客户端与维度校验；BGE-M3 是**对称**模型，
    查询和文档用同一种编码方式即可，不需要加"query: "前缀之类的指令。
    """
    if not text or not text.strip():
        raise AppException(ErrorCode.PARAM_ERROR, "查询内容不能为空")

    client = _get_client()
    try:
        vector = client.embed_query(text)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_error(exc) from exc
    return _check_dimension(list(vector))
