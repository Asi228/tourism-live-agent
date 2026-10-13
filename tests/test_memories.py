"""Phase 11 长期记忆的测试。

盯的是**工程行为**，不是"记得好不好"：
  开关到底有没有生效？记忆会不会串到别人那里？存量到顶是"拒绝"还是"静默丢"？
  拼进提示词的是"最近更新在前"吗？超预算时丢的是最旧的吗？Agent 那条路也吃了记忆吗？

两条外部依赖全部替掉（检索 + LLM），一次真实 API 都不打。
"""

import pytest

from app.agent import graph as graph_mod
from app.config import settings
from app.core.response import ErrorCode
from app.rag import prompt
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


def _out(text: str) -> dict:
    return {"answer": text, "usage": {"total_tokens": 5}, "finish_reason": "stop"}


@pytest.fixture
def chat_env(monkeypatch):
    """纯 RAG 侧的替身；把**完整的 system** 记下来，用来断言记忆段进没进提示词。"""
    box: dict = {"results": [_hit()], "chat": []}

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        return {
            "query": query, "top_k": top_k, "searched_kb_ids": [1],
            "results": box["results"], "reranked": False, "candidate_k": top_k,
        }

    def fake_chat(system, user_msg, history=None):
        box["chat"].append({"system": system, "user": user_msg, "history": history})
        return _out("答 [1]")

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(rag_service.llm, "chat", fake_chat)
    return box


@pytest.fixture
def agent_env(monkeypatch):
    """Agent 侧的替身（LLM + 检索工具），同样记录完整 system。"""
    box: dict = {"results": [_hit()], "chat": []}

    def fake_chat(system, user_msg, max_tokens=None, temperature=None, history=None):
        box["chat"].append({"system": system, "user": user_msg})
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out("NEED")
        if system == graph_mod.EVALUATE_SYSTEM:
            return _out("SUFFICIENT")
        return _out("入职满一年可享受带薪年假 5 天 [1]。")

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        return {
            "results": box["results"], "searched_kb_ids": kb_ids or [1],
            "reranked": False, "skipped_orphans": 0,
        }

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    monkeypatch.setattr(graph_mod.tools, "search_knowledge", fake_search)
    return box


def _ask(client, user, query: str = "年假几天", **extra) -> dict:
    return client.post(
        "/api/chat", json={"query": query, **extra}, headers=user["headers"]
    ).json()


def _remember(client, user, content: str, **extra) -> dict:
    return client.post(
        "/api/memories", json={"content": content, **extra}, headers=user["headers"]
    ).json()


# ==================== 接口基本行为 ====================


def test_remember_then_list(client, make_user):
    """写进去就能读出来，且带 source_conversation_id。"""
    u = make_user()
    created = _remember(client, u, "我在财务部")
    assert created["code"] == 0, created
    assert created["data"]["content"] == "我在财务部"
    assert created["data"]["source_conversation_id"] is None

    listed = client.get("/api/memories", headers=u["headers"]).json()
    assert [m["content"] for m in listed["data"]] == ["我在财务部"]


def test_delete_memory(client, make_user):
    u = make_user()
    memory_id = _remember(client, u, "我在财务部")["data"]["id"]

    assert client.delete(f"/api/memories/{memory_id}", headers=u["headers"]).json()["code"] == 0
    assert client.get("/api/memories", headers=u["headers"]).json()["data"] == []
    # 再删一次 → 已经不是他的（不在了）
    again = client.delete(f"/api/memories/{memory_id}", headers=u["headers"]).json()
    assert again["code"] == ErrorCode.NOT_FOUND


def test_memories_are_isolated_between_users(client, make_user):
    """甲的记性不能长在乙身上 —— 读不到、更删不掉（一律 404，不是 403）。"""
    a, b = make_user(), make_user()
    memory_id = _remember(client, a, "我在财务部")["data"]["id"]

    assert client.get("/api/memories", headers=b["headers"]).json()["data"] == []
    assert (
        client.delete(f"/api/memories/{memory_id}", headers=b["headers"]).json()["code"]
        == ErrorCode.NOT_FOUND
    )
    # 甲的还在
    assert len(client.get("/api/memories", headers=a["headers"]).json()["data"]) == 1


def test_content_validation(client, make_user):
    """空内容与超长内容都当场拒绝 —— 超长不静默截断。"""
    u = make_user()
    assert _remember(client, u, "   ")["code"] == ErrorCode.PARAM_ERROR
    too_long = "啊" * 2001
    assert _remember(client, u, too_long)["code"] == ErrorCode.PARAM_ERROR


def test_per_user_cap_rejects_new_memory(client, make_user, monkeypatch):
    """存量到顶 → **拒绝写入并提示**，不静默丢最旧的。"""
    monkeypatch.setattr(settings, "USER_MEMORY_MAX_PER_USER", 1)
    u = make_user()
    assert _remember(client, u, "第一条")["code"] == 0

    second = _remember(client, u, "第二条")
    assert second["code"] == ErrorCode.RESOURCE_LIMIT_EXCEEDED, second
    # 第一条必须还在（拒绝新建 ≠ 删旧的）
    assert [m["content"] for m in client.get("/api/memories", headers=u["headers"]).json()["data"]] == [
        "第一条"
    ]


def test_source_conversation_must_be_owned(client, make_user):
    """带别人的 conversation_id 写记忆 → 404，且一条都不该写进去。"""
    a, b = make_user(), make_user()
    conv_id = _ask(client, b)["data"]["conversation_id"]

    res = _remember(client, a, "我在财务部", conversation_id=conv_id)
    assert res["code"] == ErrorCode.NOT_FOUND, res
    assert client.get("/api/memories", headers=a["headers"]).json()["data"] == []


def test_endpoints_require_auth(client):
    assert client.get("/api/memories").json()["code"] == ErrorCode.UNAUTHORIZED
    assert client.post("/api/memories", json={"content": "x"}).json()["code"] == ErrorCode.UNAUTHORIZED


# ==================== 拼接与开关 ====================


def test_build_memory_section_is_pure():
    """纯函数：空、关、超预算、单条超长 —— 四种边界一次钉住。"""
    assert prompt.build_memory_section([], 100) == ""
    assert prompt.build_memory_section(["我在财务部"], 0) == ""          # 预算 0 = 不要记忆
    assert prompt.build_memory_section(["", "   "], 100) == ""           # 只有空白也算空

    section = prompt.build_memory_section(["我在财务部"], 100)
    assert section.startswith(prompt.MEMORY_HEADER) and "- 我在财务部" in section

    # 超预算：传入顺序是"最近更新在前"，所以**丢的是排在后面的（更旧的）**
    section = prompt.build_memory_section(["新" * 5, "旧" * 30], 40)
    assert "新" in section and "旧" not in section, section

    # 第一条就超长：截断它，但至少留一条
    section = prompt.build_memory_section(["长" * 500], 30)
    assert section.startswith(prompt.MEMORY_HEADER)


def test_disabled_by_default_leaves_prompt_untouched(client, make_user, chat_env, monkeypatch):
    """开关关 → 提示词**逐字**等于没做这个功能时的样子。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    u = make_user()
    _remember(client, u, "我在财务部")

    _ask(client, u)

    assert chat_env["chat"][-1]["system"] == prompt.SYSTEM_PROMPT


def test_disabled_switch_still_allows_writing(client, make_user, monkeypatch):
    """开关只管"拼不拼进提示词"，**不拦写入** —— 这是刻意的语义。

    让用户可以平时慢慢积累、需要时再打开开关用。钉住它是为了防止谁"顺手"
    在 `create_memory` 里加个 gate，把那句注释里的承诺悄悄改掉。
    """
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    u = make_user()

    assert _remember(client, u, "我在财务部")["code"] == 0
    assert len(client.get("/api/memories", headers=u["headers"]).json()["data"]) == 1


def test_enabled_injects_memory_into_prompt(client, make_user, chat_env, monkeypatch):
    """开关开 + 有记忆 → 记忆进提示词，且仍然以原系统提示词开头（只是追加）。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    u = make_user()
    _remember(client, u, "我在财务部")

    _ask(client, u)

    system = chat_env["chat"][-1]["system"]
    assert system.startswith(prompt.SYSTEM_PROMPT)
    assert "我在财务部" in system


def test_enabled_but_no_memory_leaves_prompt_untouched(client, make_user, chat_env, monkeypatch):
    """开关开着但一条记忆都没有 → 不该凭空多出一段空表头。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    u = make_user()

    _ask(client, u)

    assert chat_env["chat"][-1]["system"] == prompt.SYSTEM_PROMPT


def test_max_items_limits_what_goes_into_prompt(client, make_user, chat_env, monkeypatch):
    """`USER_MEMORY_MAX_ITEMS` 只管"送进提示词几条"，**不动库里的数据**。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    monkeypatch.setattr(settings, "USER_MEMORY_MAX_ITEMS", 1)
    u = make_user()
    _remember(client, u, "第一条记忆")
    _remember(client, u, "第二条记忆")

    _ask(client, u)

    system = chat_env["chat"][-1]["system"]
    assert "第二条记忆" in system        # 最近更新的那条
    assert "第一条记忆" not in system
    # 库里两条都在 —— 这一项是可逆的，与 USER_MEMORY_MAX_PER_USER 不是一回事
    assert len(client.get("/api/memories", headers=u["headers"]).json()["data"]) == 2


def test_agent_path_also_gets_memory(client, make_user, agent_env, monkeypatch):
    """Agent 那条路走的是同一个 memory_service —— 不能只有纯 RAG 受益。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    u = make_user()
    _remember(client, u, "我在财务部")

    body = client.post(
        "/api/agent/chat", json={"query": "年假几天"}, headers=u["headers"]
    ).json()
    assert body["code"] == 0, body

    # 生成节点的 system（既不是 analyze 也不是 evaluate 的那次调用）
    generate_systems = [
        c["system"]
        for c in agent_env["chat"]
        if c["system"] not in (graph_mod.ANALYZE_SYSTEM, graph_mod.EVALUATE_SYSTEM)
    ]
    assert generate_systems, "没有发生生成调用"
    assert "我在财务部" in generate_systems[-1]


# ==================== 编辑与单条开关（PATCH） ====================


def test_patch_updates_content(client, make_user):
    """改正文，且响应回显改后的值。"""
    u = make_user()
    mem_id = _remember(client, u, "我在财务部")["data"]["id"]

    res = client.patch(
        f"/api/memories/{mem_id}", json={"content": "我在技术部"}, headers=u["headers"]
    ).json()
    assert res["code"] == 0, res
    assert res["data"]["id"] == mem_id
    assert res["data"]["content"] == "我在技术部"

    listed = client.get("/api/memories", headers=u["headers"]).json()["data"]
    assert [m["content"] for m in listed] == ["我在技术部"]


def test_patch_rejects_empty_and_blank_content(client, make_user):
    """空串由 schema 的 min_length 拦（40000），全空白由 service 拦（同码）。

    两条路都要钉：只钉一条的话，另一条被删掉时这里还是绿的。
    """
    u = make_user()
    mem_id = _remember(client, u, "我在财务部")["data"]["id"]

    for bad in ("", "   "):
        res = client.patch(
            f"/api/memories/{mem_id}", json={"content": bad}, headers=u["headers"]
        ).json()
        assert res["code"] == ErrorCode.PARAM_ERROR, (bad, res)

    # 原文没被改坏
    listed = client.get("/api/memories", headers=u["headers"]).json()["data"]
    assert listed[0]["content"] == "我在财务部"


def test_patch_requires_at_least_one_field(client, make_user):
    """两个字段都不给是调用方的 bug —— 返回 40000 而不是静默返回 200。"""
    u = make_user()
    mem_id = _remember(client, u, "我在财务部")["data"]["id"]

    res = client.patch(f"/api/memories/{mem_id}", json={}, headers=u["headers"]).json()
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_patch_other_users_memory_is_not_found(client, make_user):
    """别人的 id 一律 404（403 等于白送"这个 id 存在"）。"""
    a, b = make_user(), make_user()
    mem_id = _remember(client, b, "我在财务部")["data"]["id"]

    res = client.patch(
        f"/api/memories/{mem_id}", json={"content": "hacked"}, headers=a["headers"]
    ).json()
    assert res["code"] == ErrorCode.NOT_FOUND, res
    # 对方的数据没被动过
    assert client.get("/api/memories", headers=b["headers"]).json()["data"][0]["content"] == "我在财务部"


def test_disabled_memory_is_not_injected_into_prompt(client, make_user, chat_env, monkeypatch):
    """🔴 核心新行为：关掉一条 → 不进提示词，但**数据仍在列表里**。

    前半段"开着时确实进得去"是前提校验 —— 没有它，后半段那个 not in 可能是
    "从来没进过去"造成的假绿。
    """
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    u = make_user()
    mem_id = _remember(client, u, "我在财务部")["data"]["id"]

    _ask(client, u)
    assert "我在财务部" in chat_env["chat"][-1]["system"]

    res = client.patch(
        f"/api/memories/{mem_id}", json={"is_enabled": False}, headers=u["headers"]
    ).json()
    assert res["code"] == 0, res
    assert res["data"]["is_enabled"] is False

    _ask(client, u)
    assert "我在财务部" not in chat_env["chat"][-1]["system"]

    # 关掉只是不拼进提示词 —— 列表里照样看得见，才谈得上"开回来"
    listed = client.get("/api/memories", headers=u["headers"]).json()["data"]
    assert [m["content"] for m in listed] == ["我在财务部"]
    assert listed[0]["is_enabled"] is False


def test_reenabled_memory_comes_back_into_prompt(client, make_user, chat_env, monkeypatch):
    """关掉再开回来 → 重新进提示词。可逆，这正是开关与删除的本质区别。"""
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    u = make_user()
    mem_id = _remember(client, u, "我在财务部")["data"]["id"]

    client.patch(f"/api/memories/{mem_id}", json={"is_enabled": False}, headers=u["headers"])
    client.patch(f"/api/memories/{mem_id}", json={"is_enabled": True}, headers=u["headers"])

    _ask(client, u)
    assert "我在财务部" in chat_env["chat"][-1]["system"]


def test_cap_counts_disabled_memories_too(client, make_user, monkeypatch):
    """存量上限数的是**全部**条（含关掉的）。

    它管"库里存多少"（不可逆），与 `USER_MEMORY_MAX_ITEMS` 管的"送进提示词多少"
    （可逆）不是一回事 —— 关掉一条不该就此腾出容量。
    """
    monkeypatch.setattr(settings, "USER_MEMORY_MAX_PER_USER", 1)
    u = make_user()
    mem_id = _remember(client, u, "第一条")["data"]["id"]

    client.patch(f"/api/memories/{mem_id}", json={"is_enabled": False}, headers=u["headers"])
    res = _remember(client, u, "第二条")
    assert res["code"] == ErrorCode.RESOURCE_LIMIT_EXCEEDED, res


def test_max_items_counts_only_enabled(client, make_user, chat_env, monkeypatch):
    """`USER_MEMORY_MAX_ITEMS` 只数**启用中**的几条 —— 关掉的不占名额。

    与上一条正好是一对：上限（存量）数全部，配额（进提示词）只数启用的。
    """
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", True)
    monkeypatch.setattr(settings, "USER_MEMORY_MAX_ITEMS", 1)
    u = make_user()
    _remember(client, u, "旧的一条")
    newer = _remember(client, u, "新的一条")["data"]["id"]

    # 把更新的那条关掉 → 名额应该让给旧的那条
    client.patch(f"/api/memories/{newer}", json={"is_enabled": False}, headers=u["headers"])

    _ask(client, u)
    system = chat_env["chat"][-1]["system"]
    assert "旧的一条" in system
    assert "新的一条" not in system
