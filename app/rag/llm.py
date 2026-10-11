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
from collections.abc import Iterator

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
            # 流式也要拿到用量。langchain-openai 只在"没传自定义 base_url
            # （或走 LangSmith 网关）"时才自动开 stream_usage；我们传了
            # base_url，不显式传的话请求里就没有 stream_options={"include_usage": true}，
            # 上游不会在流末尾给 usage —— done.usage 恒为 null（前端 tokens 标签
            # 消失、meta 里也没用量、成本对比表在流式这条路上断掉）。
            # 该参数只影响流式（_stream/_astream 里才会加 stream_options），
            # 非流式的 invoke 不受影响；DeepSeek 要求 stream_options 必须与
            # stream:true 同传 —— langchain 只在流式路径加，不会踩那个 400。
            stream_usage=True,
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


def _build_messages(
    system: str, user: str, history: list[tuple[str, str]] | None
) -> list[tuple[str, str]]:
    """把 system / history / user 拼成 LangChain 的消息列表。

    `chat()` 与 `stream_chat()` **共用这一份**：两条路只要有一处拼得不一样，
    就会出现"流式答出来的东西和非流式不同"这种极难排查的问题。
    （同 `prompt.build_prompt_and_sources()` 的教训。）
    """
    messages: list[tuple[str, str]] = [("system", system)]
    for role, text in history or []:
        messages.append(("human" if role == "user" else "ai", text))
    messages.append(("human", user))
    return messages


def _usage_from(metadata: dict | None) -> dict | None:
    """LangChain 的 usage_metadata → 项目统一的三字段形状。没有就返回 None。"""
    if not metadata:
        return None
    return {
        "input_tokens": metadata.get("input_tokens"),
        "output_tokens": metadata.get("output_tokens"),
        "total_tokens": metadata.get("total_tokens"),
    }


def chat(
    system: str,
    user: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    history: list[tuple[str, str]] | None = None,
) -> dict:
    """一次问答，返回 `{"answer": str, "usage": {...} | None, "finish_reason": str | None}`。

    `max_tokens` / `temperature` 传了就用 `bind()` 覆盖本次调用的参数
    （客户端是单例，不能为了改两个参数就重建）。Agent 的"决策调用"就靠这个：
    它只需要回答一个词，把 max_tokens 压到个位数，既省钱又好解析。

    `history` 是**之前几轮的问答对**，格式 `[("user", 问题), ("assistant", 答案), …]`
    （Phase 9 的多轮对话）。两个刻意的取舍：
      · **只带问答对，不带当时检索到的资料** —— 否则每轮的参考资料都会堆进上下文，
        token 会随轮数平方级上涨；资料本来就该由本轮检索重新提供。
      · role 用项目统一的 `"user"` / `"assistant"`（见 models/message.py 的 `ROLE_*`）。
        这里不 import 那个常量：`rag` 与 `models` 是平级模块，不该互相依赖。

    不做重试：RAG 的目标是"答不出来就说答不出来"，失败时快速返回并让用户重试，
    比在服务端闷头重试更符合预期（重试还会重复计费）。
    """
    client = _get_client()
    overrides: dict = {}
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if temperature is not None:
        overrides["temperature"] = temperature
    if overrides:
        client = client.bind(**overrides)

    messages = _build_messages(system, user, history)

    try:
        message = client.invoke(messages)
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


def stream_chat(
    system: str,
    user: str,
    history: list[tuple[str, str]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Iterator[dict]:
    """流式问答：逐块产出 `{"type": "delta", "text": …}`，
    最后一块是 `{"type": "end", "usage": …|None, "finish_reason": …|None}`。

    ⚠️ **异常是在"迭代过程中"抛出来的**，而那时 HTTP 响应头早就发出去了 ——
    上层**改不了状态码**，只能用 `event: error` 告诉前端。
    这正是 `chat()` 保持原样、没被改成"包一层 stream"的原因：
    非流式那条路必须继续能返回统一信封（`{code, message, data}`）。

    `usage` **尽力而为**：OpenAI 兼容协议要在请求里显式带
    `stream_options={"include_usage": true}` 才会在末尾给用量，上游是否支持不能假定。
    取不到就是 `None` —— 绝不因为它缺失而让整个流失败，那是本末倒置。

    "答案是不是空的"**不在这里判**：流式下"一个字都没吐"可能是上游异常，
    也可能是正常但空，交给调用方在流结束后统一判断（它才知道整段拼起来是什么）。
    """
    client = _get_client()
    overrides: dict = {}
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if temperature is not None:
        overrides["temperature"] = temperature
    if overrides:
        client = client.bind(**overrides)

    messages = _build_messages(system, user, history)

    usage: dict | None = None
    finish_reason: str | None = None
    try:
        for chunk in client.stream(messages):
            text = chunk.content
            # 新版 LangChain 的 content 可能是结构化分片（多模态），这里只取纯文本
            if isinstance(text, str) and text:
                yield {"type": "delta", "text": text}
            got = _usage_from(getattr(chunk, "usage_metadata", None))
            if got:
                usage = got
            reason = (getattr(chunk, "response_metadata", None) or {}).get("finish_reason")
            if reason:
                finish_reason = reason
    except Exception as exc:  # noqa: BLE001 — 上游异常类型不稳定，统一包装
        # 注意：`GeneratorExit`（消费方关闭生成器）继承自 BaseException，不会被这里吞掉，
        # 客户端中途断开时异常能正常向上传播。
        raise _wrap_error(exc) from exc

    yield {"type": "end", "usage": usage, "finish_reason": finish_reason}
