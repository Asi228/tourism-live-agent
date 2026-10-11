"""Phase 9 会话与记忆的测试。

盯的是**工程行为**，不是"答得好不好"：
  会话会不会重复创建？历史到底有没有进 Prompt？截断是不是按"轮"而不是按"条"？
  甲乙之间能不能串会话？删会话有没有把消息一起带走？没命中的那一轮算不算数？

两条外部依赖全部替掉（检索 + LLM），一次真实 API 都不打。
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agent import graph as graph_mod
from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models import Conversation, Message
from app.services import rag_service, vector_service


def _hit(chunk_id: int = 1, content: str = "员工入职满一年可享受带薪年假 5 天。") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": 1,
        "document_name": "员工手册.txt",
        "knowledge_base_id": 1,
        "page_number": None,
        "score": 0.9,
        "content": content,
    }


@pytest.fixture
def chat_env(monkeypatch):
    """替掉检索与 LLM。`chat` 记录每次生成调用（含 history），供断言历史。"""
    box: dict = {"results": [_hit()], "chat": []}

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        return {
            "query": query, "top_k": top_k, "searched_kb_ids": [1],
            "results": box["results"], "reranked": False, "candidate_k": top_k,
        }

    def fake_chat(system, user_msg, history=None):
        box["chat"].append({"system": system, "user": user_msg, "history": history})
        return {
            "answer": "答 %d" % len(box["chat"]),
            "usage": {"total_tokens": 10},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(rag_service.llm, "chat", fake_chat)
    return box


@pytest.fixture
def agent_env(monkeypatch):
    """Agent 侧的替身（只在测"两个接口共用会话"时用）。"""
    box: dict = {"chat": []}

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        box["chat"].append({"system": system, "history": history})
        if system == graph_mod.ANALYZE_SYSTEM:
            return {"answer": "NEED", "usage": {}, "finish_reason": "stop"}
        if system == graph_mod.EVALUATE_SYSTEM:
            return {"answer": "SUFFICIENT", "usage": {}, "finish_reason": "stop"}
        return {"answer": "Agent 答", "usage": {"total_tokens": 5}, "finish_reason": "stop"}

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        return {"results": [_hit()], "searched_kb_ids": [1],
                "reranked": False, "skipped_orphans": 0}

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    monkeypatch.setattr(graph_mod.tools, "search_knowledge", fake_search)
    return box


def _ask(client, user, query: str = "年假几天", **extra) -> dict:
    payload = {"query": query, **extra}
    return client.post("/api/chat", json=payload, headers=user["headers"]).json()


# ==================== 会话的创建与复用 ====================


def test_first_turn_creates_conversation(client, make_user, chat_env, session_factory):
    """不传 conversation_id → 后端隐式建一个，并把新 id 放回响应。"""
    admin = make_user("admin")

    body = _ask(client, admin, "年假几天")
    assert body["code"] == 0, body

    conv_id = body["data"]["conversation_id"]
    assert conv_id, "第一次提问没有创建会话"

    with session_factory() as db:
        conv = db.get(Conversation, conv_id)
        assert conv is not None
        assert conv.user_id == admin["id"]
        assert conv.title == "年假几天", "标题应当取自首个问题"
        rows = db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()

    assert [m.role for m in rows] == ["user", "assistant"]
    assert rows[0].content == "年假几天"
    assert rows[1].sources, "assistant 消息要带上引用列表（重放历史时要用）"
    assert rows[1].meta and rows[1].meta["hit"] is True


def test_second_turn_reuses_conversation_and_carries_history(client, make_user, chat_env):
    """带同一个 id 再问 → 复用会话，且上一轮问答被带进 Prompt。"""
    admin = make_user("admin")

    first = _ask(client, admin, "年假几天")["data"]
    second = _ask(client, admin, "那病假呢", conversation_id=first["conversation_id"])["data"]

    assert second["conversation_id"] == first["conversation_id"], "不该又开一个新会话"

    history = chat_env["chat"][-1]["history"]
    assert history == [
        ("user", "年假几天"),
        ("assistant", first["answer"]),
    ], "上一轮的问答对没有按顺序进 Prompt"


def test_history_is_capped_by_config(client, make_user, chat_env, monkeypatch):
    """历史只带最近 N 轮 —— 而且必须按「轮」裁，不能按「条」裁。"""
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", 1)
    admin = make_user("admin")

    d1 = _ask(client, admin, "第一问")["data"]
    conv_id = d1["conversation_id"]
    _ask(client, admin, "第二问", conversation_id=conv_id)
    _ask(client, admin, "第三问", conversation_id=conv_id)

    history = chat_env["chat"][-1]["history"]
    assert len(history) == 2, "配置成 1 轮就只该带 2 条消息"
    assert history[0] == ("user", "第二问")
    assert history[1][0] == "assistant", "裁完必须以 user 开头，不能只剩一个孤立的答案"


def test_zero_turns_means_no_history(client, make_user, chat_env, monkeypatch):
    """配成 0 → 退回单轮行为（这是用来对比"多轮到底有没有用"的开关）。"""
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", 0)
    admin = make_user("admin")

    d1 = _ask(client, admin, "年假几天")["data"]
    _ask(client, admin, "那病假呢", conversation_id=d1["conversation_id"])

    assert chat_env["chat"][-1]["history"] == []


def test_no_hit_turn_is_also_saved(client, make_user, chat_env, session_factory):
    """没命中的那一轮也要落库 —— 用户问过什么，刷新后该看得到。"""
    chat_env["results"] = []
    admin = make_user("admin")

    data = _ask(client, admin)["data"]
    assert data["hit"] is False
    assert data["conversation_id"], "没命中也要有会话"

    with session_factory() as db:
        rows = db.scalars(
            select(Message).where(Message.conversation_id == data["conversation_id"])
            .order_by(Message.id)
        ).all()
    assert [m.role for m in rows] == ["user", "assistant"]
    assert rows[1].meta["hit"] is False


# ==================== 归属：不能串会话 ====================


def test_other_users_conversation_is_not_found(client, make_user, chat_env):
    """拿别人的 conversation_id → 404「会话不存在」，不泄露"这个 id 是存在的"。"""
    owner = make_user("admin")
    other = make_user("employee")
    conv_id = _ask(client, owner, "年假几天")["data"]["conversation_id"]

    body = _ask(client, other, "那病假呢", conversation_id=conv_id)
    assert body["code"] == ErrorCode.NOT_FOUND, body

    # 详情和删除同样拦住
    assert client.get(f"/api/conversations/{conv_id}",
                      headers=other["headers"]).json()["code"] == ErrorCode.NOT_FOUND
    assert client.delete(f"/api/conversations/{conv_id}",
                         headers=other["headers"]).json()["code"] == ErrorCode.NOT_FOUND


def test_nonexistent_conversation_is_not_found(client, make_user, chat_env):
    admin = make_user("admin")
    assert _ask(client, admin, conversation_id=99999999)["code"] == ErrorCode.NOT_FOUND


# ==================== 列表 / 详情 / 删除 ====================


def test_list_shows_count_and_orders_by_recent(client, make_user, chat_env):
    admin = make_user("admin")

    old = _ask(client, admin, "先问的")["data"]["conversation_id"]
    new = _ask(client, admin, "后问的")["data"]["conversation_id"]

    items = {i["id"]: i for i in client.get("/api/conversations", headers=admin["headers"]).json()["data"]}
    assert old in items and new in items
    assert items[new]["message_count"] == 2
    assert items[old]["title"] == "先问的"

    ids = [i["id"] for i in client.get("/api/conversations", headers=admin["headers"]).json()["data"]]
    assert ids.index(new) < ids.index(old), "最近聊过的应当排在前面"


def test_detail_returns_messages_in_order(client, make_user, chat_env):
    admin = make_user("admin")
    conv_id = _ask(client, admin, "年假")["data"]["conversation_id"]
    _ask(client, admin, "病假", conversation_id=conv_id)

    detail = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert detail["id"] == conv_id
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["sources"]


def test_delete_conversation_removes_its_messages(client, make_user, chat_env, session_factory):
    admin = make_user("admin")
    conv_id = _ask(client, admin, "年假几天")["data"]["conversation_id"]

    assert client.delete(f"/api/conversations/{conv_id}",
                         headers=admin["headers"]).json()["code"] == 0

    with session_factory() as db:
        assert db.get(Conversation, conv_id) is None
        left = db.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
        )
    assert left == 0, "删了会话却留下消息"


# ==================== 与 Agent 侧共用 ====================


def test_agent_and_rag_share_the_same_conversation(
    client, make_user, chat_env, agent_env, session_factory
):
    """两个问答接口共用同一套会话表 —— 可以在一段对话里轮流用（这是刻意的口径）。"""
    admin = make_user("admin")

    first = _ask(client, admin, "年假几天")["data"]           # 纯 RAG 开头
    body = client.post(
        "/api/agent/chat",
        json={"query": "那病假呢", "conversation_id": first["conversation_id"]},
        headers=admin["headers"],
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["conversation_id"] == first["conversation_id"], "Agent 侧另开了会话"

    # Agent 的**生成**节点应当看到了上一轮（上一轮是纯 RAG 产生的）
    gen = [c for c in agent_env["chat"] if c["system"].startswith("你是企业知识库的问答助手")]
    assert gen, "生成节点没被调用"
    assert gen[-1]["history"] == [("user", "年假几天"), ("assistant", first["answer"])]

    with session_factory() as db:
        rows = db.scalars(
            select(Message).where(Message.conversation_id == first["conversation_id"])
            .order_by(Message.id)
        ).all()
    assert [m.role for m in rows] == ["user", "assistant", "user", "assistant"]


def test_agent_decision_nodes_do_not_see_history(client, make_user, agent_env):
    """判定节点（analyze/evaluate）刻意**不看**历史 —— 判的是这句话本身。

    混进上文只会让"要不要查库""资料够不够"变模糊，还白花 token。
    """
    admin = make_user("admin")
    d1 = client.post("/api/agent/chat", json={"query": "年假几天"},
                     headers=admin["headers"]).json()["data"]
    client.post("/api/agent/chat",
                json={"query": "那病假呢", "conversation_id": d1["conversation_id"]},
                headers=admin["headers"])

    analyze_calls = [c for c in agent_env["chat"] if c["system"] == graph_mod.ANALYZE_SYSTEM]
    evaluate_calls = [c for c in agent_env["chat"] if c["system"] == graph_mod.EVALUATE_SYSTEM]
    for c in analyze_calls + evaluate_calls:
        assert c["history"] is None, "判定节点不该拿到历史"


# ==================== 失败路径：不留半截 ====================


def test_llm_failure_leaves_no_conversation(client, make_user, chat_env, session_factory, monkeypatch):
    """LLM 挂了这一轮不落库 —— 历史里不该留一条没有答案的半截对话。

    实现上靠的是"新会话只 flush 不 commit"：异常冒出去后 session 关闭，
    未提交的会话自动消失。

    ⚠️ 断言用"前后差值"而不是"总数为 0"：**测试库是整个 session 共享的**，
    别的用例早就建过会话了（这个坑在 Phase 4 / Phase 6 各踩过一次）。
    """

    def boom(system, user_msg, history=None):
        raise AppException(ErrorCode.LLM_ERROR, "大模型调用失败")

    monkeypatch.setattr(rag_service.llm, "chat", boom)
    admin = make_user("admin")

    with session_factory() as db:
        before = db.scalar(select(func.count()).select_from(Conversation))

    body = _ask(client, admin, "年假几天")
    assert body["code"] == ErrorCode.LLM_ERROR, body

    with session_factory() as db:
        after = db.scalar(select(func.count()).select_from(Conversation))

    assert after == before, "问答失败了，却留下了一个会话"


def test_agent_failure_leaves_no_conversation(client, make_user, session_factory, monkeypatch):
    """Agent 侧失败也不许留空壳会话 —— 与纯 RAG 是**同一条保证、两份实现**。

    它盯的是 agent_service 里那句 `db.rollback()`：失败路径是「rollback → 写审计 →
    commit」，若把 rollback 当成多余删掉，只 flush 的新会话就会跟着写审计那次 commit
    一起落库，会话表开始长空壳 —— 而其余用例照样全绿。
    （纯 RAG 那侧由 test_llm_failure_leaves_no_conversation 盯着，两条路都得有。）
    """
    from app.services import agent_service

    admin = make_user("admin")

    def boom(*a, **kw):
        raise RuntimeError("graph exploded")

    # agent_service 用的是 `from app.agent.graph import build_graph`，名字绑在它自己的
    # 命名空间里 —— 要 patch agent_service.build_graph，patch 源模块不会生效。
    monkeypatch.setattr(
        agent_service, "build_graph",
        lambda *a, **kw: type("G", (), {"invoke": staticmethod(boom)})(),
    )

    with session_factory() as db:
        before = db.scalar(select(func.count()).select_from(Conversation))

    body = client.post(
        "/api/agent/chat", json={"query": "年假几天"}, headers=admin["headers"]
    ).json()
    assert body["code"] == ErrorCode.LLM_ERROR, body

    with session_factory() as db:
        after = db.scalar(select(func.count()).select_from(Conversation))
    assert after == before, "Agent 问答失败了，却留下了一个会话"


# ==================== 列表分页 ====================


def test_conversation_list_is_pageable(client, make_user, chat_env):
    """列表必须能翻到更早的会话 —— 它是拿到历史会话的**唯一**入口。

    只给 limit 不给 offset 时，第 N 条之后的会话在界面上永远不可达。
    这里用「整表」和「翻页拼起来」对拍：顺序、边界、不重不漏一次看全。
    """
    admin = make_user("admin")
    for i in range(3):
        _ask(client, admin, "第%d问" % i)

    def list_page(limit: int, offset: int) -> list[dict]:
        r = client.get(
            "/api/conversations",
            params={"limit": limit, "offset": offset},
            headers=admin["headers"],
        ).json()
        assert r["code"] == 0, r
        return r["data"]

    full = list_page(100, 0)
    assert len(full) >= 3, "本用例自己就建了 3 个会话"

    paged = list_page(2, 0) + list_page(2, 2)
    assert [i["id"] for i in paged] == [i["id"] for i in full[:4]], "翻页与整表顺序对不上"
    assert list_page(2, 100000) == [], "offset 远超总数应当返回空列表"


def test_conversation_list_rejects_bad_paging_params(client, make_user):
    """limit 有上下界、offset 不能为负。

    不校验的话 `limit=0` 会静默返回空列表 —— 看起来像"这个用户没有会话"，
    其实是参数写错了；`limit=-1` 在 SQL 里更会直接报错。
    """
    admin = make_user("admin")
    for query in ("limit=0", "limit=101", "offset=-1"):
        body = client.get(f"/api/conversations?{query}", headers=admin["headers"]).json()
        assert body["code"] == ErrorCode.PARAM_ERROR, (query, body)


# ==================== 撤销授权后的回放脱敏 ====================


def test_revoked_access_hides_answer_and_sources(client, make_user, chat_env, monkeypatch):
    """撤销授权后，历史回放不能变成绕开撤销的后门。

    口径：**收回的是资料，不是用户自己敲的字** ——
    assistant 的答案正文与引用整条隐藏，user 自己的提问与会话标题保留。
    """
    from app.services import knowledge_service

    admin = make_user("admin")
    conv_id = _ask(client, admin, "销售底薪多少")["data"]["conversation_id"]

    before = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert before["messages"][1]["sources"], "撤权前本来就该看得到引用"
    assert "答 1" in before["messages"][1]["content"]

    # 模拟"撤销了他对这个库的 read"：可访问集合变空（管理员本来是不受限的）
    monkeypatch.setattr(knowledge_service, "get_accessible_kb_ids", lambda db, user: [])

    after = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert after["messages"][0]["content"] == "销售底薪多少", "用户自己问的话应当还在"
    assert after["messages"][1]["sources"] == [], "引用没有被抹掉"
    assert "答 1" not in after["messages"][1]["content"], "答案正文没有被隐藏"
    assert after["messages"][1]["meta"]["redacted"] is True

    # 会话本身还在列表里（标题是用户自己的问题，不是被收回的资料）
    ids = [i["id"] for i in client.get("/api/conversations", headers=admin["headers"]).json()["data"]]
    assert conv_id in ids


def test_display_redaction_does_not_touch_stored_data(
    client, make_user, chat_env, monkeypatch, session_factory
):
    """脱敏只作用于**回放这一层**，绝不能把结果写回数据库。

    这是最容易写错的地方：直接改 ORM 对象的 content/sources 会被 SQLAlchemy 记成脏字段，
    之后只要这个 session 上还有任何一次 commit，脱敏结果就被永久写进库 ——
    原答案再也拿不回来（不可逆的数据损坏）。

    ⚠️ 所以断言必须落在 **`session.dirty`** 上，而不是"再查一次 DB"：
    走 HTTP 接口验证是抓不到这个错的 —— 那个 request 的 session 自始至终没有 commit，
    脏字段随 session 关闭被丢掉，DB 看起来一切正常。必须在同一个 session 里查脏。
    """
    from app.models import Message
    from app.services import conversation_service, knowledge_service

    admin = make_user("admin")
    conv_id = _ask(client, admin, "销售底薪多少")["data"]["conversation_id"]
    monkeypatch.setattr(knowledge_service, "get_accessible_kb_ids", lambda db, user: [])

    with session_factory() as db:
        # user 只用来判权限，而这个函数在测试里已被替掉，所以给个带 id 的壳就够
        user = SimpleNamespace(id=admin["id"])
        conv = conversation_service.get_owned(db, user, conv_id)
        msgs = conversation_service.load_messages_for_display(db, user, conv)

        assert msgs[1]["content"] == conversation_service.REDACTED_ANSWER
        assert not db.dirty, "脱敏动了 ORM 对象，下一次 commit 就会把隐藏结果写回库"

        db.commit()   # 就算这里 commit，也不该有任何东西被写进去
        rows = db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()
        assert rows[1].content == "答 1", "脱敏结果被写回库里了"
        assert rows[1].sources, "引用被写回库里了"


# ==================== 历史：无依据的轮次不进 Prompt ====================


def test_history_skips_turns_without_evidence(client, make_user, chat_env):
    """「没找到资料」的轮次整轮不进历史 —— 那句固定话术不能当事实传下去。"""
    admin = make_user("admin")
    first = _ask(client, admin, "第一问")["data"]

    chat_env["results"] = []          # 第二轮：没命中
    _ask(client, admin, "第二问", conversation_id=first["conversation_id"])
    chat_env["results"] = [_hit()]    # 第三轮：正常
    _ask(client, admin, "第三问", conversation_id=first["conversation_id"])

    history = chat_env["chat"][-1]["history"]
    assert history == [
        ("user", "第一问"),
        ("assistant", first["answer"]),
    ], "无依据的轮次应当整轮丢掉（包括它自己那个问题）"


# ==================== 数据保留 ====================


def test_conversation_quota_rejects_new_conversation(client, make_user, chat_env, monkeypatch):
    """到配额就**拒绝新建并提示**，而不是悄悄删掉最旧的会话。

    另外要确认它只拦"新建"：接着已有会话继续问必须照常 ——
    否则用户会被卡在"什么都干不了"的状态里，那比配额本身更糟。
    """
    admin = make_user("admin")
    first = _ask(client, admin, "第一问")["data"]["conversation_id"]

    # 用"当前实际条数"当配额，避免依赖"每个用例拿到全新用户"这个假设
    existing = len(
        client.get("/api/conversations?limit=100", headers=admin["headers"]).json()["data"]
    )
    monkeypatch.setattr(settings, "CONVERSATION_MAX_PER_USER", existing)

    body = _ask(client, admin, "想新开一段")
    assert body["code"] == ErrorCode.RESOURCE_LIMIT_EXCEEDED, body
    assert "上限" in body["message"]

    assert _ask(client, admin, "第二问", conversation_id=first)["code"] == 0


def test_message_cap_trims_oldest_whole_turns(client, make_user, chat_env, monkeypatch):
    """超上限时按**整轮**删最旧的（一轮 = 一问一答）。"""
    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 4)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["data"]["conversation_id"]
    for i in (2, 3, 4):
        _ask(client, admin, "第%d问" % i, conversation_id=conv_id)

    detail = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert len(detail["messages"]) == 4, "没裁到上限（或多裁了）"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"], \
        "留下了没有对应问题的孤立消息"
    assert detail["messages"][0]["content"] == "第3问", "删掉的不是最旧的那一轮"


def test_message_cap_rounds_to_whole_turn(client, make_user, chat_env, monkeypatch):
    """上限是奇数时**宁可少留一条**，也要保持整轮。

    删到 5 条会留下一个落单的消息；存库的形态和喂模型的形态该是同一套标准。
    """
    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 5)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["data"]["conversation_id"]
    for i in (2, 3):
        _ask(client, admin, "第%d问" % i, conversation_id=conv_id)

    detail = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert len(detail["messages"]) == 4, "奇数上限应当向下取到整轮"


def test_trim_failure_does_not_lose_the_turn(
    client, make_user, chat_env, session_factory, monkeypatch
):
    """保留清理失败，不能连累本轮问答 —— SAVEPOINT 要保证的就是这件事。

    ⚠️ 这里刻意用**真的会报错的 SQL**（不存在的表）来制造失败，而不是在 Python 里 `raise`：
    只有走到 DB 层的错误才可能把 session 弄进"必须回滚"的状态；Python 异常模拟不出来，
    那样写出来的用例对旧实现也会通过，等于什么都没测。
    """
    from sqlalchemy import text

    from app.repositories import message_repository

    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 1)   # 逼它每轮都触发清理

    def boom(db, conversation_id, count):
        db.execute(text("SELECT * FROM __no_such_table__"))

    monkeypatch.setattr(message_repository, "delete_oldest", boom)
    admin = make_user("admin")

    body = _ask(client, admin, "年假几天")
    assert body["code"] == 0, body

    with session_factory() as db:
        rows = db.scalars(
            select(Message)
            .where(Message.conversation_id == body["data"]["conversation_id"])
            .order_by(Message.id)
        ).all()
    assert [m.role for m in rows] == ["user", "assistant"], "清理失败把这一轮问答也带走了"


def test_retention_off_keeps_everything(client, make_user, chat_env, monkeypatch):
    """0 = 不限，一项关掉不影响另一项。"""
    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 0)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_PER_USER", 0)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["data"]["conversation_id"]
    for i in range(2, 6):
        _ask(client, admin, "第%d问" % i, conversation_id=conv_id)

    detail = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    assert len(detail["messages"]) == 10, "上限关掉之后不该删任何东西"


def test_history_backfills_past_no_evidence_turns(client, make_user, chat_env, monkeypatch):
    """最近几轮都没依据时要**继续往前取**，而不是把历史交白卷。

    这条盯着 `_HISTORY_OVERFETCH`：只取 turns*2 条再筛，无依据的轮次会把配额吃光，
    用户配的轮数被悄悄打了折。
    """
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", 1)
    admin = make_user("admin")
    first = _ask(client, admin, "第一问")["data"]
    conv_id = first["conversation_id"]

    chat_env["results"] = []
    _ask(client, admin, "第二问", conversation_id=conv_id)
    chat_env["results"] = [_hit()]     # 第三轮要有命中，否则压根不会调 LLM、也看不到 history
    _ask(client, admin, "第三问", conversation_id=conv_id)

    assert chat_env["chat"][-1]["history"] == [
        ("user", "第一问"),
        ("assistant", first["answer"]),
    ], "多取再筛没生效，历史被无依据的轮次顶空了"
