"""知识库 FAQ（常见问题置顶）。

测三件事：权限口径（读=read / 写=admin）、上限、以及"它不参与检索"这条边界。
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import FAQ_MAX_PER_KB, KnowledgeBase, KnowledgeBaseFaq, Message


def _make_kb(client, admin_headers, **extra) -> int:
    payload = {"name": "kb-" + uuid.uuid4().hex[:10], "description": "测试", **extra}
    r = client.post("/api/knowledge", json=payload, headers=admin_headers)
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _add_faq(client, headers, kb_id, question="年假几天", answer="满一年 5 天"):
    return client.post(
        "/api/faqs",
        json={"knowledge_base_id": kb_id, "question": question, "answer": answer},
        headers=headers,
    ).json()


# ==================== 读 ====================


def test_list_needs_only_read_permission(client, make_user):
    """有 read 就能看 FAQ —— 它不是管理功能，是给普通使用者的。"""
    admin, employee = make_user("admin"), make_user()
    kb_id = _make_kb(client, admin["headers"])

    assert client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": employee["id"], "permission": "read"},
        headers=admin["headers"],
    ).json()["code"] == 0

    assert _add_faq(client, admin["headers"], kb_id)["code"] == 0
    got = client.get(f"/api/faqs?kb_id={kb_id}", headers=employee["headers"]).json()
    assert got["code"] == 0, got
    assert [f["question"] for f in got["data"]] == ["年假几天"]


def test_list_without_permission_is_not_found(client, make_user):
    admin, other = make_user("admin"), make_user()
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id)

    got = client.get(f"/api/faqs?kb_id={kb_id}", headers=other["headers"]).json()
    assert got["code"] == ErrorCode.NOT_FOUND, got


def test_list_empty_is_not_an_error(client, make_user):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    got = client.get(f"/api/faqs?kb_id={kb_id}", headers=admin["headers"]).json()
    assert got["code"] == 0 and got["data"] == []


# ==================== 写 ====================


def test_write_needs_kb_admin(client, make_user):
    """read / write 都不够，必须是库的 admin。"""
    admin, employee = make_user("admin"), make_user()
    kb_id = _make_kb(client, admin["headers"])

    for perm in ("read", "write"):
        client.post(
            f"/api/knowledge/{kb_id}/members",
            json={"user_id": employee["id"], "permission": perm},
            headers=admin["headers"],
        )
        res = _add_faq(client, employee["headers"], kb_id)
        assert res["code"] == ErrorCode.NOT_FOUND, (perm, res)


def test_kb_admin_can_add_and_edit_and_delete(client, make_user, session_factory):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    created = _add_faq(client, admin["headers"], kb_id)
    assert created["code"] == 0, created
    faq_id = created["data"]["id"]
    assert created["data"]["answer"] == "满一年 5 天"

    patched = client.patch(
        f"/api/faqs/{faq_id}", json={"answer": "满一年 5 天，满三年 10 天"},
        headers=admin["headers"],
    ).json()
    assert patched["code"] == 0, patched
    assert patched["data"]["answer"] == "满一年 5 天，满三年 10 天"

    assert client.delete(f"/api/faqs/{faq_id}", headers=admin["headers"]).json()[
        "code"
    ] == 0
    with session_factory() as db:
        assert db.scalar(select(KnowledgeBaseFaq)) is None


def test_update_requires_at_least_one_field(client, make_user):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    faq_id = _add_faq(client, admin["headers"], kb_id)["data"]["id"]

    res = client.patch(f"/api/faqs/{faq_id}", json={}, headers=admin["headers"]).json()
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_blank_question_or_answer_is_rejected(client, make_user):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    for bad in ({"question": "   ", "answer": "x"}, {"question": "x", "answer": "  "}):
        res = _add_faq(client, admin["headers"], kb_id, **bad)
        assert res["code"] == ErrorCode.PARAM_ERROR, (bad, res)


def test_cap_is_enforced(client, make_user):
    """一个库最多 `FAQ_MAX_PER_KB` 条 —— 超过一屏就失去"置顶"的意义了。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    for i in range(FAQ_MAX_PER_KB):
        assert _add_faq(
            client, admin["headers"], kb_id, question=f"问题{i}"
        )["code"] == 0

    res = _add_faq(client, admin["headers"], kb_id, question="多出来的一条")
    assert res["code"] == ErrorCode.RESOURCE_LIMIT_EXCEEDED, res


def test_unknown_faq_is_not_found(client, make_user):
    admin = make_user("admin")
    assert client.patch(
        "/api/faqs/999999", json={"answer": "x"}, headers=admin["headers"]
    ).json()["code"] == ErrorCode.NOT_FOUND
    assert client.delete("/api/faqs/999999", headers=admin["headers"]).json()[
        "code"
    ] == ErrorCode.NOT_FOUND


def test_endpoints_require_auth(client):
    assert client.get("/api/faqs?kb_id=1").json()["code"] == ErrorCode.UNAUTHORIZED
    assert client.post("/api/faqs", json={}).json()["code"] == ErrorCode.UNAUTHORIZED


# ==================== 边界：FAQ 不参与检索 ====================


def test_faq_answer_is_not_vectorized(client, make_user, session_factory):
    """🔴 FAQ 的答案**不进向量库**：它是命中即返回，与 RAG 两条独立的路。

    这条断言守住的是可溯源性 —— 一旦有人把 FAQ 也灌进 Milvus，
    "这个答案来自哪份文档"就说不清了，而那正是这套系统最值钱的地方。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, answer="只有 FAQ 里有这句话")

    with session_factory() as db:
        # FAQ 单独一张表，文档切分块里不该出现它的内容
        from app.models import DocumentChunk

        chunks = list(db.scalars(select(DocumentChunk)))
        assert all("只有 FAQ 里有这句话" not in c.content for c in chunks)
        # 也没有为它造出文档
        from app.models import Document

        assert list(db.scalars(select(Document))) == []
        # 库本身还在（不是因为库没了才查不到）
        assert db.get(KnowledgeBase, kb_id) is not None


# ==================== 命中即返回（问答入口的闸门）====================


@pytest.fixture
def rag_env(monkeypatch):
    """记录 RAG 侧被调用的次数 —— 用来断言 FAQ 命中时**一次都没碰它们**。"""
    from app.services import rag_service, vector_service

    box = {"search": 0, "chat": 0}

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        box["search"] += 1
        return {
            "query": query, "top_k": top_k, "searched_kb_ids": kb_ids or [],
            "results": [], "reranked": False, "candidate_k": top_k,
        }

    def fake_chat(system, user_msg, history=None):
        box["chat"] += 1
        return {"answer": "这是模型生成的答案", "usage": {"total_tokens": 1},
                "finish_reason": "stop"}

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(rag_service.llm, "chat", fake_chat)
    return box


def test_hit_returns_standard_answer_without_search_or_model(
    client, make_user, rag_env, session_factory
):
    """🔴 核心：命中 FAQ → 直接给标准答案，**不检索、不调模型**。

    「不调模型」是这个功能的主要价值之一（省 token、统一口径），
    所以断言必须落在**调用计数**上，而不是只看返回值长什么样。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="年假几天", answer="满一年 5 天")

    res = client.post(
        "/api/chat", json={"query": "年假几天"}, headers=admin["headers"]
    ).json()
    assert res["code"] == 0, res
    data = res["data"]
    assert data["answer"] == "满一年 5 天"
    assert data["answer_mode"] == "faq"
    assert data["faq_id"] is not None
    assert data["sources"] == [], "FAQ 答案没有文档引用，sources 必须是空的"
    assert data["model"] is None, "没调模型就不该伪造一个模型名"
    assert data["usage"] is None
    assert data["searched_kb_ids"] == [kb_id]
    assert data["conversation_id"] is not None

    assert rag_env["search"] == 0, "命中 FAQ 后不该再检索"
    assert rag_env["chat"] == 0, "命中 FAQ 后不该再调模型"


def test_hit_falls_back_to_the_faq_library_scope(client, make_user, rag_env):
    """`searched_kb_ids` 诚实记下"答案出自哪个库" —— 便于回看与后续收紧口径。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="报销标准", answer="住宿 900")

    data = client.post(
        "/api/chat", json={"query": "报销标准"}, headers=admin["headers"]
    ).json()["data"]
    assert data["searched_kb_ids"] == [kb_id]


def test_turn_is_persisted_with_faq_meta(client, make_user, rag_env, session_factory):
    """FAQ 那一轮照样进会话历史，且 meta 里带得动"这是 FAQ 答的"。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="餐补多少", answer="每天 30")

    conv_id = client.post(
        "/api/chat", json={"query": "餐补多少"}, headers=admin["headers"]
    ).json()["data"]["conversation_id"]

    with session_factory() as db:
        msgs = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conv_id)
                .order_by(Message.id)
            )
        )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "每天 30"
    assert msgs[1].meta["answer_mode"] == "faq"
    assert msgs[1].meta["source"] == "faq"
    assert msgs[1].sources == []


def test_exact_match_only(client, make_user, rag_env):
    """**精确匹配，不做模糊匹配** —— 宁可漏，不可错。

    用户问"年假能不能延到下一年"，被模糊命中到"年假几天"的标准答案，
    拿到的是"看着权威、其实答非所问"的东西，比走一趟 RAG 差得多。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="年假几天", answer="满一年 5 天")

    for q in ("年假几天？", "请问年假几天", "年假 几天"):
        data = client.post(
            "/api/chat", json={"query": q}, headers=admin["headers"]
        ).json()["data"]
        assert data.get("answer_mode") != "faq", f"{q!r} 不该命中 FAQ"
    assert rag_env["search"] >= 3, "没命中就该照常走检索"


def test_out_of_scope_kb_does_not_match(client, make_user, rag_env, session_factory):
    """FAQ 只在**本次检索范围内**匹配 —— 选了别的库就不该命中。"""
    admin = make_user("admin")
    kb_a = _make_kb(client, admin["headers"])
    kb_b = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_a, question="年假几天", answer="满一年 5 天")

    data = client.post(
        "/api/chat", json={"query": "年假几天", "kb_ids": [kb_b]},
        headers=admin["headers"],
    ).json()["data"]
    assert data.get("answer_mode") != "faq"
    assert data.get("faq_id") is None


def test_user_without_permission_does_not_match(client, make_user, rag_env):
    """没权限的库里的 FAQ 不该命中（否则 FAQ 成了绕开权限的后门）。"""
    admin, other = make_user("admin"), make_user()
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="年假几天", answer="满一年 5 天")

    data = client.post(
        "/api/chat", json={"query": "年假几天"}, headers=other["headers"]
    ).json()["data"]
    assert data.get("answer_mode") != "faq"
    assert rag_env["search"] >= 1


def test_stream_endpoint_returns_envelope_on_faq_hit(client, make_user, rag_env):
    """流式端点命中 FAQ 时返回**统一信封**而不是 SSE。

    答案是现成的，没有"逐字生成"这个过程，硬包成 SSE 只是徒增解析成本。
    这是**成功路径走信封的唯一情况**，所以前端必须按 code 先判成功再判失败
    （见 `frontend/src/api/stream.js`）。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="年假几天", answer="满一年 5 天")

    res = client.post(
        "/api/chat/stream", json={"query": "年假几天"}, headers=admin["headers"]
    )
    assert res.status_code == 200
    assert "text/event-stream" not in res.headers.get("content-type", "")
    body = res.json()
    assert body["code"] == 0, body
    assert body["data"]["answer"] == "满一年 5 天"
    assert body["data"]["answer_mode"] == "faq"
    assert rag_env["chat"] == 0


def test_agent_endpoints_also_short_circuit(client, make_user, rag_env, monkeypatch):
    """🔴 **Agent 那条路也要过这道闸门**。

    否则用户切到 Agent 模式点同一个 FAQ 卡片，又拿到模型生成的答案 ——
    "同一句话两个答案来源"的问题原样还在。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    _add_faq(client, admin["headers"], kb_id, question="年假几天", answer="满一年 5 天")

    # 非流式
    data = client.post(
        "/api/agent/chat", json={"query": "年假几天"}, headers=admin["headers"]
    ).json()["data"]
    assert data["answer"] == "满一年 5 天"
    assert data["answer_mode"] == "faq"
    # Agent 侧必填字段要有，否则 response_model 校验会炸
    assert data["iterations"] == 0
    assert data["steps"] == []
    assert "elapsed_ms" in data

    # 流式
    res = client.post(
        "/api/agent/chat/stream", json={"query": "年假几天"}, headers=admin["headers"]
    )
    assert "text/event-stream" not in res.headers.get("content-type", "")
    assert res.json()["data"]["answer"] == "满一年 5 天"

    assert rag_env["chat"] == 0, "Agent 那两条路也不该调模型"
