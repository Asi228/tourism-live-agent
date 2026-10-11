"""LLM 客户端构造参数的守卫。

`stream_usage` 这类"不传也不报错、只是静默少个字段"的参数最容易在重构里丢：
丢了的症状是流式 `done.usage` 恒为 null、前端 tokens 标签消失、`messages.meta`
里也没有用量 —— 没有任何报错，只有成本统计悄悄断掉。所以这里直接钉**构造参数**。

外部依赖全部替掉（不建真的 ChatOpenAI、不打真实 API）。
"""

from app.config import settings
from app.rag import llm


def test_client_requests_stream_usage(monkeypatch):
    """`_get_client()` 必须显式传 `stream_usage=True`。

    langchain-openai 只在"没传自定义 base_url（或走 LangSmith 网关）"时才自动开
    stream_usage；我们传了 `base_url=settings.LLM_BASE_URL`，条件不成立 ——
    不显式传的话，流式请求里就没有 `stream_options={"include_usage": true}`，
    上游不会在流末尾给用量，`done.usage` 恒为 null。
    （该参数只影响 `_stream`/`_astream`，非流式的 `invoke` 不受影响。）
    """
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key", raising=False)
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", _FakeClient)
    # 单例置空，强制 `_get_client` 真的走一遍构造（monkeypatch 会自动还原）
    monkeypatch.setattr(llm, "_client", None)

    llm._get_client()

    assert captured.get("stream_usage") is True, (
        "stream_usage 丢了：流式的 done.usage 会恒为 null，tokens 标签消失"
    )
    # 关思考是同一个构造函数里的另一条硬约定，顺手一起钉住
    assert captured.get("extra_body") == llm.THINKING_DISABLED
