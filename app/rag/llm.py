"""LLM 调用 —— LangChain ChatOpenAI 接 DeepSeek。

为什么用 LangChain 而不是自己拼 HTTP：
  Phase 8 的 LangGraph 节点之间传的就是 LangChain 的 Message 对象，
  现在这一层用 LangChain，Phase 8 可以直接接上，不用重写。
  （另一条路是原生 httpx，依赖更少但后续要自己搭桥。）

⚠️ 关思考模式 —— 本文件最关键的三行：
  DeepSeek 的 `deepseek-flash` **默认开启思考**，实测同一个问题
  completion_tokens 从 6 涨到 36（121 字的思考过程也计费）。
  实测有效的关闭参数只有 `thinking={"type":"disabled"}`（`reasoning_effort="none"` 同样有效，
  但 `enable_thinking=False` / `reasoning={"enabled":false}` / `chat_template_kwargs` 都关不掉）。
  传参位置必须是 **extra_body**：
    · extra_body={"thinking": ...}   ✅ 生效
    · model_kwargs={"thinking": ...} ❌ TypeError: Completions.create() got an
                                          unexpected keyword argument 'thinking'
"""

import logging

from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

# 关思考模式。见文件头说明，别挪到 model_kwargs 里。
THINKING_DISABLED = {"thinking": {"type": "disabled"}}

_client: ChatOpenAI | None = None


def _get_client() -> ChatOpenAI:
    """惰性单例：只有真正要问答时才创建，避免没配 key 就启动失败。"""
    global _client
    if _client is None:
        if not settings.LLM_API_KEY:
            raise AppException(ErrorCode.LLM_ERROR, "未配置 LLM_API_KEY")

        _client = ChatOpenAI(
            model=settings.LLM_MODEL,
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
            extra_body=THINKING_DISABLED,  # ← 关思考，见文件头
        )
        logger.info(
            "LLM 客户端就绪 model=%s base_url=%s 思考模式=关闭",
            settings.LLM_MODEL, settings.LLM_BASE_URL,
        )
    return _client


def _wrap_error(exc: Exception) -> AppException:
    """把第三方异常收敛成我们自己的错误码。

    上游（DeepSeek）的错误是 **HTTP 400 + OpenAI 风格的 error body**，
    和我们"HTTP 恒 200、真实码在 body"的约定相反，所以必须在这里统一转换，
    不让上游格式漏到 API 层。

    上游原文**只进日志、不进响应**：里面可能带 base_url、模型名、参数，
    属于内部细节。响应统一给一句话，口径与 api/health.py、rag/embedding.py 一致。
    """
    logger.warning("LLM 调用失败: %s", str(exc)[:400])
    return AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")


def chat(system: str, user: str) -> dict:
    """一次问答，返回 `{"answer": str, "usage": {...} | None, "finish_reason": str | None}`。

    不做重试：RAG 的目标是"答不出来就说答不出来"，失败时快速返回并让用户重试，
    比在服务端闷头重试更符合预期（重试还会重复计费）。
    """
    client = _get_client()
    try:
        message = client.invoke([("system", system), ("human", user)])
    except Exception as exc:  # noqa: BLE001 — 上游异常类型不稳定，统一包装
        raise _wrap_error(exc) from exc

    answer = (message.content or "").strip()
    if not answer:
        # 空答案通常是触发了长度限制或上游异常，直接告诉用户重试，
        # 不要把空字符串当成"模型说没有相关信息"返回给前端。
        logger.warning("LLM 返回空内容 finish_reason=%s", (message.response_metadata or {}).get("finish_reason"))
        raise AppException(ErrorCode.LLM_ERROR, "大模型未返回内容，请稍后重试")

    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "answer": answer,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
        "finish_reason": (message.response_metadata or {}).get("finish_reason"),
    }
