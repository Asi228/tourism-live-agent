"""Phase 14 Agent 流式（SSE，节点级进度）的测试。

两条外部依赖照 P8 的方式替掉（LLM 与检索工具），**绝不打真实大模型**。

这里最要紧的一条是 **图必须用流式阶段新开的 session**（见
`test_graph_uses_stream_session_not_request_one`）—— 它是这条路上唯一"只在流式、
只在生产炸，同步路径一条测试都红不了"的坑。
"""

import json

import pytest
from sqlalchemy import func, select, text

from app.agent import graph as graph_mod
from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db import session as db_session
from app.models import Conversation, Message
from app.services import agent_service, conversation_service


def _hit(chunk_id: int = 1, name: str = "员工手册.txt",
         content: str = "员工入职满一年可享受带薪年假 5 天。") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": 1,
        "document_name": name,
        "knowledge_base_id": 1,
        "page_number": None,
        "score": 0.9,
        "content": content,
    }


def _out(text: str) -> dict:
    return {
        "answer": text,
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "finish_reason": "stop",
    }


@pytest.fixture
def agent_env(monkeypatch, session_factory):
    """替掉 LLM 与检索工具，并把**流式落库**指向测试库。

    ⚠️ 流式那几段用的 session **只有一个来源**：`app.db.session.new_session`。
    换这一处就够 —— 以前 `agent_service` / `conversation_service` 各存一份
    `_session_factory`，fixture 里得写两遍警告，漏一个就静默写进**真 MySQL**。
    """
    monkeypatch.setattr(db_session, "new_session", session_factory)

    box: dict = {
        "analyze": "NEED",
        "evaluate": "SUFFICIENT",
        # 可选：按调用次序给不同判定（模拟"第一轮不够 → 换个词再检索一轮"）
        "evaluate_seq": None,
        "_eval_calls": 0,
        "results": [_hit()],
        "chat_error_on": None,
    }

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        if box["chat_error_on"] and box["chat_error_on"] in system:
            raise AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out(box["analyze"])
        if system == graph_mod.EVALUATE_SYSTEM:
            if box["evaluate_seq"]:
                idx = min(box["_eval_calls"], len(box["evaluate_seq"]) - 1)
                box["_eval_calls"] += 1
                return _out(box["evaluate_seq"][idx])
            return _out(box["evaluate"])
        if system == graph_mod.DIRECT_SYSTEM:
            return _out("你好，我是企业知识库助手。")
        return _out("入职满一年可享受带薪年假 5 天 [1]。")

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        # 检索节点没有降级设计（判定节点才有），拿它当中途失败的注入点最干净
        if box.get("search_error"):
            raise box["search_error"]
        return {
            "results": box["results"],
            "searched_kb_ids": kb_ids or [1],
            "reranked": False,
            "skipped_orphans": 0,
        }

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    monkeypatch.setattr(graph_mod.tools, "search_knowledge", fake_search)
    box["search_error"] = None
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


def _stream(client, user, body: dict):
    """发起一次 Agent 流式请求，返回 `(状态码, content-type, [(event, data), …])`。"""
    with client.stream(
        "POST", "/api/agent/chat/stream", json=body, headers=user["headers"]
    ) as resp:
        return resp.status_code, resp.headers.get("content-type", ""), _parse_sse(resp)


def _names(frames) -> list[str]:
    return [ev for ev, _ in frames]


# ==================== 正常路径 ====================


def test_stream_emits_status_steps_meta_done(client, make_user, agent_env):
    """事件序列是 status → step*N → meta → done。

    step 中间㧟的是**多步决策的进度**（analyze/retrieve/evaluate/generate），
    不是逐字 —— Agent 的价值在"它为什么慢"，这是 P14 定的口径。
    """
    user = make_user("admin")

    code, ctype, frames = _stream(client, user, {"query": "年假几天"})
    assert code == 200
    assert "text/event-stream" in ctype

    assert _names(frames)[0] == "status"
    assert _names(frames)[-1] == "done"

    steps = [d for ev, d in frames if ev == "step"]
    assert [s["node"] for s in steps] == [
        "analyze", "retrieve", "evaluate", "generate",
    ], "节点级进度没按顺序吐出来"

    metas = [d for ev, d in frames if ev == "meta"]
    assert len(metas) == 1
    assert metas[0]["hit"] is True
    assert metas[0]["sources"], "引用应该在答案之前就给出来"

    done = frames[-1][1]
    assert done["answer"] == "入职满一年可享受带薪年假 5 天 [1]。"
    # 24 = 12(analyze) + 12(generate)：Agent 的用量是**多次调用累加**的
    # （`graph._accumulate`），一次问答花了多少必须能一眼看清 —— 这也是
    # "Agent 比纯 RAG 贵多少"能对比出来的前提。
    assert done["usage"]["total_tokens"] == 24
    assert done["saved"] is True
    assert done["conversation_id"], "新会话 id 只在 done 里给"
    assert len(done["steps"]) == 4, "轨迹要完整，它是'这次为什么绕了两圈'的唯一证据"


def test_stream_persists_a_turn(client, make_user, agent_env, session_factory):
    """答完之后这一步进了库：两条消息 + meta 里的 `source: agent`。"""
    user = make_user("admin")

    _, _, frames = _stream(client, user, {"query": "年假几天"})
    conv_id = frames[-1][1]["conversation_id"]

    with session_factory() as db:
        after = db.scalar(select(func.count()).select_from(Message))
        rows = (
            db.execute(select(Message).where(Message.conversation_id == conv_id))
            .scalars()
            .all()
        )
    assert after == 2, "一轮问答恰好写两条消息（多写=重复落库，少写=丢了）"
    assert [m.role for m in rows] == ["user", "assistant"]
    assert rows[-1].meta["source"] == "agent", "回放历史时据此决定要不要画执行轨迹"
    assert rows[-1].meta["steps"], "轨迹要跟着存，否则回放时看不到"


# ==================== 准备阶段失败仍走统一信封 ====================


def test_other_users_conversation_rejected_before_stream(client, make_user, agent_env):
    """🔴 越权会话必须在**响应开始之前** 404 —— 降级成流里的 error 是错的。

    这也是 P13 `stream_answer` 是普通函数而非生成器的全部理由：准备工作要赶在
    FastAPI 开始迭代 body 之前跑完，否则状态码已经发出去了。
    """
    owner = make_user("employee")
    other = make_user("employee")

    first = client.post(
        "/api/agent/chat", json={"query": "先建个会话"}, headers=owner["headers"]
    ).json()["data"]
    conv_id = first["conversation_id"]

    resp = client.post(
        "/api/agent/chat/stream",
        json={"query": "年假", "conversation_id": conv_id},
        headers=other["headers"],
    )
    assert resp.status_code == 200, "HTTP 恒 200，真错在 body.code"
    assert "text/event-stream" not in resp.headers.get("content-type", ""), (
        "越权不该返回一个 SSE 流"
    )
    assert resp.json()["code"] == ErrorCode.NOT_FOUND


def test_disabled_agent_rejected_before_stream(client, make_user, agent_env, monkeypatch):
    """Agent 未启用同样是响应前的失败 —— 走信封而不是流。"""
    user = make_user("admin")
    monkeypatch.setattr(settings, "AGENT_ENABLED", False)

    resp = client.post(
        "/api/agent/chat/stream", json={"query": "年假"}, headers=user["headers"]
    )
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    assert resp.json()["code"] == ErrorCode.PARAM_ERROR


# ==================== 流中途失败 ====================


def test_stream_error_does_not_persist(client, make_user, agent_env, session_factory):
    """多步推理半途挂掉：给 error 事件、**不落库**。

    半截答案进 messages 会污染后续的喂回（模型会顺着"没答完"的口径走），
    与"LLM 失败不留空壳"是同一个口径。失败审计照旧写一条。
    """
    user = make_user("admin")
    # ⚠️ 别拿 evaluate 节点做这个实验：它失败时会**降级**成"资料足够"继续走
    # （P8 刻意的：宁可少岔一轮，也不能让整个问答崩），根本不会冒泡到这里。
    # retrieve 节点没有这层兜底，是干净的注入点。
    agent_env["search_error"] = AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")

    _, _, frames = _stream(client, user, {"query": "年假几天"})

    errors = [d for ev, d in frames if ev == "error"]
    assert len(errors) == 1, "失败要给 error 事件"
    assert errors[0]["code"] == ErrorCode.LLM_ERROR
    assert _names(frames)[-1] == "error", "error 之后不该再有 done"

    with session_factory() as db:
        after = db.scalar(select(func.count()).select_from(Message))
    assert after == 0, "半截的一轮不进历史"


# ==================== 流式路径独有的坑 ====================


def test_graph_uses_stream_session_not_request_one(
    client, make_user, agent_env, monkeypatch, session_factory
):
    """🔴 **图必须用流式阶段新开的 session 装配，绝不能是请求那个。**

    `build_graph(db, user, ...)` 是**闭包捕获 db** 的（graph.py 有注释解释为什么），
    而请求 session 在响应体开始迭代**之前**就关了（fastapi 0.115.6 的 yield 依赖
    早退，P13 已坐实）。P13 那次只是流里碰了一个 ORM 对象，这里踩的是
    **整个图的每个节点都在碰 db** —— 一旦退回用请求 session，流式这条路全线炸，
    而 `/api/agent/chat`（同步）的用例一条都不会红。

    所以这里不猜" session 是不是活的"，而是直接比对**对象身份**：图拿到的 db
    必须是 `new_session()` 产出的那一个。
    """
    from app.services import agent_service as mod

    built_with: list = []
    _real_build = mod.build_graph

    def spy_build_graph(db, user, kb_ids=None, top_k=None):
        built_with.append(db)
        return _real_build(db, user, kb_ids, top_k)

    monkeypatch.setattr(mod, "build_graph", spy_build_graph)

    # 把工厂换成一个"记录自己产出过谁"的版本
    produced: list = []

    def spy_factory():
        s = session_factory()
        produced.append(s)
        return s

    # 收敛之后只有一个替换点：`app.db.session.new_session`
    monkeypatch.setattr(db_session, "new_session", spy_factory)

    user = make_user("admin")
    _, _, frames = _stream(client, user, {"query": "年假几天"})

    assert frames[-1][0] == "done", "这次流程本身应当是成功的"
    assert built_with, "图一次都没装配"
    # ⚠️ 比的是 `produced[0]` 而不是 `[-1]`：`new_session` 现在被**两处**共用 ——
    # `agent_service` 先开一个来跑图，`done` 之后 `conversation_service` 的收尾维护
    # 再开一个。图拿到的是**第一个**。
    assert built_with[0] is produced[0], (
        "图用的不是流式阶段新开的 session —— 它多半又抓回了请求那个，"
        "生产上会在响应开始后拿到一个已关闭的 session"
    )


# ==================== 审计失败不得牵连本轮数据 ====================


def _drop_audit_table(session_factory):
    """让审计写入**真的失败**：把 agent_logs 表干掉，flush 时就是 no such table。

    比 monkeypatch 一个"抛异常的 create"真实得多 —— 后者测的是调用方，
    前者走的是 repo 里那条真实的 `except → 保存点回滚 → 返回 None` 路径。
    """
    with session_factory() as db:
        db.execute(text("DROP TABLE agent_logs"))
        db.commit()


def test_audit_failure_does_not_take_the_conversation_with_it(
    client, make_user, agent_env, session_factory
):
    """🔴 审计写失败**不能**把已经 flush 出来的会话行一起带走。

    链条：会话行先 flush（ensure_conversation）→ 写审计失败 → 若这里是整个 session
    的 rollback，会话行会跟着消失，而 `conv.id` 那个 Python 属性**还有值** →
    紧接着的 `append_turn` 拿一个已经不存在的 id 去插消息。SQLite 默认不开外键，
    于是静默留下孤儿消息（测试一条都不红）；MySQL 上 `messages_ibfk_1` 直接 1452，
    把一次成功的问答变成 500。

    所以审计必须写在 SAVEPOINT 里（与 `prune_older_than` 同款）。
    """
    user = make_user("admin")
    _drop_audit_table(session_factory)

    r = client.post("/api/agent/chat", json={"query": "年假几天"}, headers=user["headers"])
    assert r.json()["code"] == 0, r.json()   # 审计挂了，答案照给
    conv_id = r.json()["data"]["conversation_id"]

    with session_factory() as db:
        assert db.get(Conversation, conv_id) is not None, (
            "会话被审计回滚连带带走了 —— 消息会指向一个不存在的会话"
        )
        msgs = (
            db.execute(select(Message).where(Message.conversation_id == conv_id))
            .scalars()
            .all()
        )
    assert len(msgs) == 2


def test_audit_failure_keeps_stream_answer_saved(
    client, make_user, agent_env, session_factory
):
    """流式那条路同一个不变量：审计失败不影响落库，`saved` 仍然是 true。"""
    user = make_user("admin")
    _drop_audit_table(session_factory)

    _, _, frames = _stream(client, user, {"query": "年假几天"})
    done = frames[-1][1]
    assert done["saved"] is True, "审计写不进去不等于这轮没存进历史"
    assert done["conversation_id"], "新会话 id 只在 done 里给"

    with session_factory() as db:
        assert db.get(Conversation, done["conversation_id"]) is not None


# ==================== 落库失败：与 RAG 同一口径 ====================


def test_stream_reports_saved_false_when_persist_fails(
    client, make_user, agent_env, monkeypatch, session_factory
):
    """落库失败 → `done{saved: false}`，**且答案照样完整交付**。

    Agent 这条路的落库原本裸奔：一次 DB 抖动就会让整个生成器炸掉，用户看着答案
    生成完了却什么都没收到（`done` 都出不来）。与 `rag_service._persist_after_stream`
    对齐后，失败降级成 `saved=false` —— 前端那句"这次没能存入历史"就是为它准备的。
    """
    user = make_user("admin")

    def boom(*args, **kwargs):
        raise RuntimeError("模拟数据库抖动")

    monkeypatch.setattr(conversation_service, "append_turn", boom)

    _, _, frames = _stream(client, user, {"query": "年假几天"})

    assert _names(frames)[-1] == "done", "落库失败也要把 done 交付出去，不能断流"
    done = frames[-1][1]
    assert done["answer"] == "入职满一年可享受带薪年假 5 天 [1]。", "答案不能因落库失败而丢"
    assert done["saved"] is False, "这是前端唯一的通知通道"


# ==================== 两条覆盖缺口 ====================


def test_smalltalk_reports_empty_scope_not_null(
    client, make_user, agent_env
):
    """寒暄直答（analyze=SKIP）：一次库都没查 → `searched_kb_ids` 必须是 `[]`。

    `None` 的语义是"管理员不受限"，把"没查库"也报成 None，等于把普通员工标成
    "扫了全库"。这条分支原本没有哨兵。
    """
    user = make_user("admin")
    agent_env["analyze"] = "SKIP"

    _, _, frames = _stream(client, user, {"query": "你好"})

    metas = [d for ev, d in frames if ev == "meta"]
    assert metas[0]["searched_kb_ids"] == [], "没查库要给 []，不能用 None（那是'不限范围'）"
    assert metas[0]["iterations"] == 0
    done = frames[-1][1]
    assert done["iterations"] == 0
    assert [s["node"] for s in [d for ev, d in frames if ev == "step"]] == [
        "analyze", "generate",
    ], "判定为不需要检索时，中间那两个节点就不该出现"


def test_multi_round_emits_retrieve_twice(client, make_user, agent_env):
    """判定"资料不够 → 换个词再检索一轮"：**retrieve 出现两次**，前端数得出轮数。

    这才是节点级进度真正要展示的场景（6 步而不是 4 步）—— 最简路径（一次命中）
    根本体现不出"多步决策"这四个字。
    """
    user = make_user("admin")
    agent_env["evaluate_seq"] = ["RETRY: 年假政策 补充条款", "SUFFICIENT"]

    _, _, frames = _stream(client, user, {"query": "年假几天"})

    steps = [d for ev, d in frames if ev == "step"]
    nodes = [s["node"] for s in steps]
    assert nodes == ["analyze", "retrieve", "evaluate", "retrieve", "evaluate", "generate"]
    assert frames[-1][1]["iterations"] == 2, "检索轮数要数得出来"


def test_step_event_tolerates_null_patch():
    """langgraph 在节点返回 `None` 时给出的补丁是 `{"node": None}` —— 补丁本身是 None。

    不兜这一下就是 AttributeError，而且它发生在**响应已经开始之后**：状态码早就
    发出去了、也来不及给 `error` 事件，前端只能看到一条静默断流。
    """
    assert agent_service._step_event("retrieve", None) == {"node": "retrieve"}
    assert agent_service._step_event("generate", {}) == {"node": "generate"}
