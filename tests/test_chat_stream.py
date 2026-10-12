"""流式问答（SSE）的测试。

盯的是**协议与落库**，不是"模型答得好不好"：

  · 事件序列对不对（meta → delta* → done）？
  · `done.answer` 是不是所有 delta 拼起来的完整答案（而不是最后一块）？
  · 流结束后到底落没落库、落的和用户看到的是不是同一段？
  · LLM 中途挂了，会不会留下半截答案？
  · 鉴权失败返回的到底是流、还是普通信封？

外部依赖全部替掉（检索 + LLM），一次真实 API 都不打。
"""

import json

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models import Conversation, Message
from app.rag import llm as llm_module
from app.rag import prompt
from app.services import conversation_service, rag_service, vector_service


def _hit(content: str = "员工入职满一年可享受带薪年假 5 天。") -> dict:
    return {
        "chunk_id": 1,
        "document_id": 1,
        "document_name": "员工手册.txt",
        "knowledge_base_id": 1,
        "page_number": None,
        "score": 0.9,
        "content": content,
    }


@pytest.fixture
def stream_env(monkeypatch, session_factory):
    """替掉检索与流式 LLM，并把**流式落库**指向测试库。

    ⚠️ 两个 `_session_factory` 都要换掉：流式路径是自己开 session 的（不能复用
    请求那个，见 `_persist_after_stream` 的说明），不换就会写进**真 MySQL**。
      · `rag_service`           —— 落库
      · `conversation_service`  —— `done` 之后的收尾维护（`maintain_after_stream`）

    `fail_at`：让流在第 N 块之前抛错，用来验证"中途失败不留半截"。
    """
    monkeypatch.setattr(rag_service, "_session_factory", session_factory)
    monkeypatch.setattr(conversation_service, "_session_factory", session_factory)

    box: dict = {
        "results": [_hit()],
        "parts": ["答案", "分", "块"],
        "fail_at": None,
        "calls": [],
    }

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        return {
            "query": query,
            "top_k": top_k,
            "searched_kb_ids": [1],
            "results": box["results"],
            "reranked": False,
            "candidate_k": top_k,
        }

    def fake_stream(system, user, history=None, max_tokens=None, temperature=None):
        box["calls"].append({"system": system, "user": user})
        for i, part in enumerate(box["parts"]):
            if box["fail_at"] is not None and i == box["fail_at"]:
                raise AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")
            yield {"type": "delta", "text": part}
        yield {
            "type": "end",
            "usage": {"input_tokens": 1, "output_tokens": 3, "total_tokens": 4},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(llm_module, "stream_chat", fake_stream)
    return box


def _parse_sse(resp) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析成 `[(event, data), …]`（事件之间以空行分隔）。"""
    out: list[tuple[str, dict]] = []
    name: str | None = None
    for line in resp.iter_lines():
        if not line:
            name = None
        elif line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((name, json.loads(line[len("data: "):])))
    return out


def _stream(client, user, body: dict, headers=None):
    """发起一次流式请求，返回 `(response, [(event, data), …])`。"""
    with client.stream(
        "POST",
        "/api/chat/stream",
        json=body,
        headers=headers if headers is not None else user["headers"],
    ) as resp:
        return resp.status_code, resp.headers.get("content-type", ""), _parse_sse(resp)


def _message_count(session_factory) -> int:
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(Message))


# ==================== 正常路径 ====================


def test_stream_emits_meta_deltas_then_done(client, make_user, stream_env):
    """事件序列是 meta → delta* → done，且 done.answer 是**所有** delta 拼起来的。

    这条钉的是最容易写错的地方：把 `done.answer` 写成"最后一块 delta"，
    界面上看不出问题（前面已经逐块渲染了），但落库就会存成残缺答案。
    """
    user = make_user("admin")

    status, ctype, events = _stream(client, user, {"query": "年假几天"})

    assert status == 200
    assert ctype.startswith("text/event-stream"), ctype
    names = [e for e, _ in events]
    assert names[0] == "status" and names[-1] == "done", names
    assert names.count("delta") == len(stream_env["parts"]), names

    deltas = "".join(d["text"] for e, d in events if e == "delta")
    done = events[-1][1]
    assert done["answer"] == deltas == "答案分块"
    assert done["saved"] is True
    assert done["conversation_id"] is not None

    meta = [d for ev, d in events if ev == "meta"][0]
    assert meta["hit"] is True
    assert meta["sources"] and meta["sources"][0]["document_name"] == "员工手册.txt"


def test_stream_persists_what_user_saw(client, make_user, stream_env, session_factory):
    """流结束后库里**确实**有一轮完整问答，且与用户看到的逐字一致。"""
    user = make_user("admin")

    _, _, events = _stream(client, user, {"query": "年假几天"})
    done = events[-1][1]
    conv_id = done["conversation_id"]

    with session_factory() as db:
        rows = db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()

    assert [m.role for m in rows] == ["user", "assistant"]
    assert rows[0].content == "年假几天"
    assert rows[1].content == done["answer"], "落库的答案与界面上看到的不是同一段"
    assert rows[1].meta["source"] == "rag"
    assert rows[1].meta["hit"] is True


def test_stream_continues_existing_conversation(
    client, make_user, stream_env, session_factory
):
    """带 conversation_id 时接着上文问：meta 回显该 id，落库也落在同一个会话里。"""
    user = make_user("admin")

    _, _, first = _stream(client, user, {"query": "第一问"})
    conv_id = first[-1][1]["conversation_id"]

    _, _, second = _stream(client, user, {"query": "第二问", "conversation_id": conv_id})
    meta_again = [d for ev, d in second if ev == "meta"][0]
    assert meta_again["conversation_id"] == conv_id, "已有会话应当在 meta 里回显"

    with session_factory() as db:
        total = db.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
        )
    assert total == 4, "第二轮应当追加在同一个会话里"


# ==================== 失败路径 ====================


def test_stream_failure_emits_error_and_saves_nothing(
    client, make_user, stream_env, session_factory
):
    """LLM 中途挂了：给出 `event: error`、**不落库**（不留半截答案）。

    半截答案进 `messages` 会污染后续的历史喂回（模型看到"说到一半"的话术更糟），
    与项目一贯的"LLM 失败不留空壳"是同一个口径。
    """
    user = make_user("admin")
    stream_env["fail_at"] = 1  # 第 2 块之前抛错 → 前端已经看到第一块了

    before = _message_count(session_factory)
    _, _, events = _stream(client, user, {"query": "年假几天"})

    names = [e for e, _ in events]
    assert names == ["status", "meta", "delta", "error"], names
    err = events[-1][1]
    assert err["code"] == ErrorCode.LLM_ERROR
    assert "大模型调用失败" in err["message"]

    assert _message_count(session_factory) == before, "失败了却把半截答案写进了库"


def test_stream_empty_answer_is_an_error(client, make_user, stream_env, session_factory):
    """一个字都没吐出来 → 同样报错且不落库（空消息进历史没有意义）。"""
    user = make_user("admin")
    stream_env["parts"] = []

    before = _message_count(session_factory)
    _, _, events = _stream(client, user, {"query": "年假几天"})

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == ErrorCode.LLM_ERROR
    assert _message_count(session_factory) == before


def test_prepare_failures_still_return_the_json_envelope(client, make_user, stream_env):
    """⚠️ 准备阶段的失败**不是 SSE**，仍然是统一信封（这是本项目最容易踩的坑）。

    `_prepare` 在响应开始**之前**跑（`stream_answer` 是普通函数、不是生成器），
    所以未认证/越权这类失败还能给出 `{code: …}`；只有"已经开始吐字之后"的错
    才降级成 `event: error`。前端必须按 content-type 分流 —— 见 `api/stream.js`。

    下面这条只钉"未认证"；"越权"由下一条用例单独钉（两者走的是同一条路径，
    但一个在依赖层、一个在 service 层，值得都留个哨兵）。
    """
    resp = client.post("/api/chat/stream", json={"query": "年假几天"})

    assert resp.status_code == 200
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    assert resp.json()["code"] == ErrorCode.UNAUTHORIZED


def test_stream_rejects_other_users_conversation(client, make_user, stream_env):
    """越权读别人的会话：普通信封 + 40400（不是流、不是 403）。"""
    owner = make_user("admin")
    other = make_user("admin")
    _, _, events = _stream(client, owner, {"query": "第一问"})
    conv_id = events[-1][1]["conversation_id"]

    resp = client.post(
        "/api/chat/stream",
        json={"query": "偷看", "conversation_id": conv_id},
        headers=other["headers"],
    )
    body = resp.json()
    assert body["code"] == ErrorCode.NOT_FOUND, body
    assert "text/event-stream" not in resp.headers.get("content-type", "")


# ==================== 没命中 ====================


def test_stream_no_hit_skips_llm(client, make_user, stream_env):
    """检索为空 → 不调模型，但**仍然要走 delta/done**（前端只写一套渲染）。"""
    user = make_user("admin")
    stream_env["results"] = []

    _, _, events = _stream(client, user, {"query": "食堂几点开饭"})

    names = [e for e, _ in events]
    assert names == ["status", "meta", "delta", "done"], names
    assert [d for ev, d in events if ev == "meta"][0]["hit"] is False
    assert [d for ev, d in events if ev == "delta"][0]["text"] == prompt.NO_HIT_ANSWER
    assert events[-1][1]["saved"] is True
    assert stream_env["calls"] == [], "没命中却调用了大模型"


def test_stream_injects_history_and_sources(client, make_user, stream_env):
    """两轮之后：模型收到的是**带引用上下文**的用户消息，且 system 是拼好的完整提示词。"""
    user = make_user("admin")

    _, _, first = _stream(client, user, {"query": "第一问"})
    conv_id = first[-1][1]["conversation_id"]
    _stream(client, user, {"query": "第二问", "conversation_id": conv_id})

    call = stream_env["calls"][-1]
    assert "员工入职满一年" in call["user"], "检索到的资料没有拼进用户消息"
    assert call["system"].startswith(prompt.SYSTEM_PROMPT[:20]), "系统提示词没走统一出口"


# ==================== 落库失败与收尾维护 ====================


def test_stream_done_saved_false_when_persist_fails(
    client, make_user, stream_env, monkeypatch
):
    """落库失败：答案照常完整给完，但 `done.saved=false`。

    这是用户知道"这轮没进历史"的**唯一通道**（配额刚好在落库时到顶、DB 抖一下
    都会走到这里）。前端靠它提示，README 靠它兑现"会让用户知道"的承诺 ——
    没有这条用例，`saved` 字段就是"后端发了、没人验证"的摆设。
    """

    def broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(rag_service, "_session_factory", broken_factory)

    _, _, events = _stream(client, make_user("admin"), {"query": "年假几天"})

    done = events[-1][1]
    assert events[-1][0] == "done"
    assert done["saved"] is False
    assert done["answer"] == "答案分块", "落库失败不该影响已经交付的答案"
    assert done["conversation_id"] is None


def test_stream_runs_summary_and_trim_after_done(
    client, make_user, stream_env, monkeypatch, session_factory
):
    """摘要与裁剪在 `done` **之后**补做（白盒顺序守卫）。

    摘要是同步的模型调用：放在 done 之前，用户最后一个字吐完了还得盯着光标
    等它 —— 非流式时藏在"整段响应"里看不出来，流式下是肉眼可见的停滞。
    这里给 `iter_sse` 挂一个间谍记录"已产出的事件帧"：摘要模型被调用时，
    **本次请求**的 `event: done` 必须已经在里面，否则收尾停滞就又回来了。

    ⚠️ 必须**单请求触发**（先往库里预置一轮旧消息，再发一次流式请求）：
    若靠连发两次请求凑触发条件，第一次请求的 done 帧会留在 frames 里，
    把维护挪到 done 之前的变异也照样绿 —— 顺序守卫就漏了。

    "先摘要、后裁剪"那条顺序不变量由 test_conversation_summary 的白盒用例钉着，
    这里不重复。
    """
    import app.api.chat as chat_api

    frames: list[str] = []
    seen_at_summary: list[str] | None = None

    real_sse = chat_api.iter_sse

    def spy_sse(events):
        for frame in real_sse(events):
            frames.append(frame.split("\n", 1)[0])
            yield frame

    monkeypatch.setattr(chat_api, "iter_sse", spy_sse)

    def fake_summary_chat(system, text, max_tokens=None, temperature=None, history=None):
        nonlocal seen_at_summary
        assert system == prompt.SUMMARY_SYSTEM, "流式路径不该走到普通生成调用"
        seen_at_summary = list(frames)
        return {
            "answer": "流式摘要 v1",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(llm_module, "chat", fake_summary_chat)
    # 压小窗口与阈值：窗口 1 轮、攒 1 轮就压缩（与 test_conversation_summary 同一套手法）
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", 1)
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_TRIGGER_TURNS", 1)

    user = make_user("admin")

    # 预置一轮旧消息：窗口 1 轮时，本轮流式一落库它就滑出窗口 →
    # 收尾维护里立刻攒够 trigger，摘要必然在**这一次请求内**被调用
    with session_factory() as db:
        conv = Conversation(user_id=user["id"], title="预置会话")
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="第0问"))
        db.add(Message(
            conversation_id=conv.id, role="assistant", content="旧答案",
            meta={"hit": True, "source": "rag"},
        ))
        db.commit()
        conv_id = conv.id

    _, _, events = _stream(client, user, {"query": "第一问", "conversation_id": conv_id})

    assert events[-1][0] == "done"
    assert seen_at_summary is not None, "摘要一次都没被调用（收尾维护没接回流式路径）"
    assert "event: done" in seen_at_summary, (
        "摘要模型调用发生在本次请求的 done 之前 —— 收尾停滞又回来了"
    )

    with session_factory() as db:
        conv = db.get(Conversation, conv_id)
    assert conv.summary == "流式摘要 v1", "摘要没写进会话"
