"""重排（Rerank）—— BAAI/bge-reranker-v2-m3 @ 硅基流动。

职责：把向量检索多召回出来的候选（默认 20 条）按「与问题的真实相关性」重新排序，
取前 N 条交给大模型。分工是：**向量检索保召回率，重排保精度**。

为什么用原生 httpx，而不像 embedding 那样用 LangChain：
  **OpenAI 兼容协议没有 rerank 端点**（OpenAI 压根没这个 API），所以 langchain-openai
  完全用不上；走 LangChain 就得再装 langchain-community 并自己找/写硅基流动的集成 ——
  为一次 HTTP 调用背一个大包不划算。httpx 从 Phase 5 起就是项目依赖，这条路**零新依赖**。

实测（2026-10-04）：
  · `POST {base}/rerank`，body = {model, query, documents[], top_n}
  · 返回 `{id, meta, results: [{index, relevance_score, document}]}`
  · **分数区分度远好于向量相似度**：相关 0.698 / 无关 0.0002（向量是 0.75 / 0.52）——
    它能把"勉强被召回、其实毫不相关"的噪声压到接近于 0
  · 批量 100 条约 1.7 秒，延迟不随条数线性增长
  · 错误是 **HTTP 400 + body 里的 code**（20012 模型不存在 / 20015 参数非法），
    与我们「HTTP 恒 200、真实码在 body」的约定相反 → 必须显式判 status_code
"""

import logging

import httpx

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)


def _endpoint() -> str:
    return settings.EMBEDDING_BASE_URL.strip().rstrip("/") + "/rerank"


def _wrap_error(exc: Exception) -> AppException:
    """把上游异常收敛成我们自己的错误码。

    复用 50007（EMBEDDING_ERROR）而不是新增一个码：rerank 和 embedding 是
    **同一家服务商、同一个 key**（硅基流动），分开两个码反而让排障更绕
    —— 看到 50007 就知道该去查硅基流动那套配置。

    上游原文只进日志、不进响应（可能带 base_url、模型名等内部信息），
    响应统一给一句话，口径与 embedding.py / llm.py 一致。
    """
    logger.warning("Rerank 调用失败: %s", str(exc)[:300])
    return AppException(ErrorCode.EMBEDDING_ERROR, "重排服务调用失败，请稍后重试")


def rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """对候选文档重排，返回 `[(候选原始下标, 相关性分数)]`，按分数降序。

    返回值用**原始下标**而不是文档本身，是为了让调用方能把分数写回它自己的数据结构
    （那些结构里还带着 chunk_id / document_id / 页码，重排不该把它们丢掉）。
    """
    if not documents:
        return []

    if not settings.EMBEDDING_API_KEY:
        raise AppException(ErrorCode.EMBEDDING_ERROR, "未配置 EMBEDDING_API_KEY")

    payload = {
        "model": settings.RERANK_MODEL,
        "query": query,
        "documents": documents,
        "top_n": max(1, min(top_n, len(documents))),
        "return_documents": False,
    }
    try:
        resp = httpx.post(
            _endpoint(),
            headers={
                "Authorization": "Bearer " + settings.EMBEDDING_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.RERANK_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — 网络层异常类型不稳定，统一包装
        raise _wrap_error(exc) from exc

    # 显式判状态码：上游是 HTTP 400 + body 里的 code，与我们"HTTP 恒 200"的约定相反
    if resp.status_code != 200:
        raise _wrap_error(RuntimeError("HTTP %s %s" % (resp.status_code, resp.text[:200])))

    try:
        data = resp.json()
        items = data.get("results") or []
    except Exception as exc:  # noqa: BLE001
        raise _wrap_error(exc) from exc

    pairs: list[tuple[int, float]] = []
    for item in items:
        index = item.get("index")
        score = item.get("relevance_score")
        if index is None or score is None:
            logger.warning("Rerank 返回项缺字段，已跳过: %s", str(item)[:120])
            continue
        if not (0 <= int(index) < len(documents)):
            # 越界说明上游返回和我们的输入对不上，硬用会错位到别的文档上
            logger.warning("Rerank 返回下标越界，已跳过: index=%s 候选数=%s", index, len(documents))
            continue
        pairs.append((int(index), float(score)))

    logger.info("Rerank 完成 candidates=%s 返回=%s model=%s", len(documents), len(pairs), settings.RERANK_MODEL)
    return pairs
