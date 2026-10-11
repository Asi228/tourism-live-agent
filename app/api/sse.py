"""SSE 帧序列化 —— 流式端点共用。

放在 api 层而不是某个 router 里，是为了让它**只有一份实现**：
`/chat/stream`（纯 RAG）与 `/agent/chat/stream`（Agent）吐的是同一套帧格式，
将来要加心跳注释帧（`: ping`）或调整分隔符，改这里就够了 ——
两份实现迟早会漂移，而 SSE 格式漂移的表现是"前端偶发丢帧"，很难查。
"""

import json
from collections.abc import Iterator

# SSE 响应头。`X-Accel-Buffering: no` 是给反向代理看的 —— nginx 默认会缓冲
# `text/event-stream`，那样"流式"会被攒成一次吐出来，白做。现在直连没影响，
# 部署时用得上。
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def iter_sse(events: Iterator[dict]) -> Iterator[str]:
    """把事件 dict 序列化成 SSE 帧：`event: xxx` + `data: {…}` + 一个空行。

    `ensure_ascii=False` 让中文原样出去（Starlette 会给 `text/*` 自动补
    `charset=utf-8`，不必自己编码）—— 响应体更小，抓包时也看得懂。
    """
    for ev in events:
        payload = json.dumps(ev["data"], ensure_ascii=False)
        yield f"event: {ev['event']}\ndata: {payload}\n\n"
