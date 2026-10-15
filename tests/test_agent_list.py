"""文档清单（LIST 模式 + list_documents 工具）的测试。

LIST 是 Agent 的第四条路由：用户问"有哪些文档"→ 直接产出目录，**不经模型**。
与 FAQ 命中即返回同一条思路 —— 能从确定性数据直接给出的，就别经过生成。
这里盯三件事：路由分叉对不对、权限有没有穿透、答案形态（catalog / sources 空）。
"""

import json
import uuid

import pytest

from app.agent import graph as graph_mod
from app.agent import tools as tools_mod
from app.db import session as db_session
from app.models import STATUS_COMPLETED, Document, KnowledgeBase, User


def _out(text: str) -> dict:
    return {
        "answer": text,
        "usage": {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        "finish_reason": "stop",
    }


@pytest.fixture
def agent(monkeypatch):
    """替掉 LLM：盒子里控制 analyze 的输出，并记录每次调用（用来断言"没有再调生成"）。"""
    box: dict = {"analyze": "LIST", "calls": []}

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        box["calls"].append(system[:20])
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out(box["analyze"])
        if system == graph_mod.DIRECT_SYSTEM:
            return _out("你好。")
        return _out("生成回答 [1]。")

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    return box


def _seed(session_factory, owner_id, *filenames, kb_name="员工手册"):
    """造一个知识库和若干 completed 文档（清单不查切块，所以不用造 chunk）。"""
    with session_factory() as db:
        kb = KnowledgeBase(name=kb_name, description="测试", owner_id=owner_id)
        db.add(kb)
        db.flush()
        for fn in filenames:
            db.add(
                Document(
                    knowledge_base_id=kb.id, uploader_id=owner_id, filename=fn,
                    stored_name=uuid.uuid4().hex + ".md", file_path="/tmp/x.md",
                    file_type="md", file_size=10, status=STATUS_COMPLETED,
                    chunk_count=1, char_count=10,
                )
            )
        db.commit()
        return kb.id


def _ask(client, user, query="我们都有哪些制度文档？"):
    return client.post(
        "/api/agent/chat", json={"query": query}, headers=user["headers"]
    ).json()


# ==================== _parse_analyze ====================


def test_parse_analyze_list_and_priorities():
    assert graph_mod._parse_analyze("LIST") == ("list", [])
    assert graph_mod._parse_analyze("list：所有文档") == ("list", [])
    # 其余分支不受影响（优先级：DOCS 前缀 → SKIP → LIST → 兜底 search）
    assert graph_mod._parse_analyze("SKIP") == ("skip", [])
    assert graph_mod._parse_analyze("DOCS: 年假") == ("docs", ["年假"])


# ==================== 端到端（非流式） ====================


def test_list_answer_is_catalog_without_generate(client, make_user, agent, session_factory):
    """清单直接产出：answer_mode=catalog、sources 空、**不再调一次生成**。"""
    admin = make_user("admin")
    _seed(session_factory, admin["id"], "员工考勤与休假管理制度.md", "费用报销管理制度.md")

    data = _ask(client, admin)["data"]

    assert data["answer_mode"] == "catalog"
    assert data["hit"] is True
    assert data["sources"] == []
    assert data["iterations"] == 0
    assert "员工考勤与休假管理制度.md" in data["answer"]
    assert "费用报销管理制度.md" in data["answer"]
    assert [s["node"] for s in data["steps"]] == ["analyze", "list_docs"]
    # 只有 analyze 那一次模型调用 —— 目录是确定性数据，不该再过一道生成
    assert len(agent["calls"]) == 1
    # 🔴 回归："我们都有哪些**制度**文档"含业务词"制度"，曾被规则短路成 SEARCH
    # → LIST 分支永远走不到（实测踩过）。断言"问的是模型"，而不是被短路。
    assert agent["calls"][0].startswith(graph_mod.ANALYZE_SYSTEM[:14]), (
        "含业务词的清单问法被规则短路了，根本没问模型，LIST 分支走不到"
    )


def test_list_without_documents_says_so(client, make_user, agent):
    admin = make_user("admin")  # 一个文档都没有
    data = _ask(client, admin)["data"]
    assert data["hit"] is False
    assert data["answer_mode"] == "no_evidence"
    assert [s["node"] for s in data["steps"]] == ["analyze", "list_docs"]
    assert len(agent["calls"]) == 1


def test_list_respects_permissions(client, make_user, agent, session_factory):
    """员工看不到无权库的文档 —— 清单不是权限旁路。"""
    admin = make_user("admin")
    emp = make_user()
    _seed(session_factory, admin["id"], "机密薪酬制度.md", kb_name="机密库")

    data = _ask(client, emp)["data"]
    assert "机密薪酬制度.md" not in data["answer"]
    assert data["hit"] is False


def test_list_truncation_noted_in_answer(
    client, make_user, agent, session_factory, monkeypatch
):
    """超过上限时答案里必须说明"只列了前 N 份"，不能假装全部。"""
    monkeypatch.setattr(tools_mod, "LIST_MAX", 2)
    admin = make_user("admin")
    _seed(session_factory, admin["id"], "甲.md", "乙.md", "丙.md")

    data = _ask(client, admin)["data"]
    assert "只列出前 2 份" in data["answer"]


def test_list_documents_tool_marks_truncated(client, make_user, session_factory, monkeypatch):
    monkeypatch.setattr(tools_mod, "LIST_MAX", 2)
    admin = make_user("admin")
    _seed(session_factory, admin["id"], "甲.md", "乙.md", "丙.md")

    with session_factory() as db:
        user = db.get(User, admin["id"])
        got = tools_mod.list_documents(db, user)
    assert len(got["items"]) == 2
    assert got["truncated"] is True


def test_catalog_answer_is_redacted_after_access_revoked(
    client, make_user, agent, session_factory, monkeypatch
):
    """撤权后回放：清单正文带【库名】+《文件名》，必须与引用答案同口径整条隐藏。

    catalog 的 sources 是空的（目录不是引用），但它**列出过哪些库、哪些文件** ——
    没有 meta.catalog_kb_ids 这条判据的话，撤回授权后历史里的清单会原样可见，
    成为绕开撤权的后门（2026-10-15 评审抓到的真实缺口）。
    """
    from app.services import knowledge_service

    admin = make_user("admin")
    kb_id = _seed(session_factory, admin["id"], "员工考勤与休假管理制度.md")
    conv_id = _ask(client, admin)["data"]["conversation_id"]

    before = client.get(
        f"/api/conversations/{conv_id}", headers=admin["headers"]
    ).json()["data"]
    assert "员工考勤与休假管理制度.md" in before["messages"][1]["content"]
    # 清单把"涉及哪些库"记进了 meta —— 撤权脱敏的判据就是它
    assert before["messages"][1]["meta"]["catalog_kb_ids"] == [kb_id]

    # 模拟"撤销了他对这个库的权限"：可访问集合变空（管理员本不受限）
    monkeypatch.setattr(knowledge_service, "get_accessible_kb_ids", lambda db, user: [])

    after = client.get(
        f"/api/conversations/{conv_id}", headers=admin["headers"]
    ).json()["data"]
    assert after["messages"][0]["content"] == "我们都有哪些制度文档？", "用户自己的提问还在"
    assert "员工考勤与休假管理制度.md" not in after["messages"][1]["content"], (
        "清单正文没有被隐藏 —— 撤回权限后仍能看到库名与文件名"
    )
    assert after["messages"][1]["meta"]["redacted"] is True
    # 隐藏时 meta 一并清空：连"列过哪个库"也不该再给
    assert "catalog_kb_ids" not in after["messages"][1]["meta"]


def test_legacy_catalog_message_is_redacted_after_access_revoked(
    client, make_user, agent, session_factory, monkeypatch
):
    """🔴 历史清单消息（2026-10-15 修复前落的库）也要被脱敏。

    修复前落的 catalog 消息 meta 里没有 catalog_kb_ids，只按新键判的话
    这批消息永久绕过撤权（评审实锤：真库有 1 条同类样本）。判定处对旧形状
    回退读 searched_kb_ids —— 对 catalog 来说它就是 list_docs 的检索范围，
    而清单列的就是 scope 内全部 completed 文档（**范围即引用**）。

    ⚠️ 必须用**员工**（而非管理员）提问：管理员落的清单 searched_kb_ids=None，
    但管理员 allowed 也是 None（永不受限），本就不该判 —— 这条用例钉的是
    "当初有权、后来被撤"的真实场景。
    """
    from sqlalchemy import select

    from app.models import KnowledgeBaseMember, Message
    from app.services import knowledge_service

    admin = make_user("admin")
    emp = make_user()
    kb_id = _seed(session_factory, admin["id"], "员工考勤与休假管理制度.md")
    with session_factory() as db:
        db.add(KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=emp["id"], permission="read"))
        db.commit()

    conv_id = _ask(client, emp)["data"]["conversation_id"]

    # 改回"修复前的旧形状"：删掉 catalog_kb_ids
    with session_factory() as db:
        msg = db.scalar(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.role == "assistant")
        )
        assert msg.meta["catalog_kb_ids"] == [kb_id]
        legacy = {k: v for k, v in msg.meta.items() if k != "catalog_kb_ids"}
        msg.meta = legacy
        db.commit()

    # 模拟"撤销了他对这个库的 read"
    monkeypatch.setattr(knowledge_service, "get_accessible_kb_ids", lambda db, user: [])

    after = client.get(
        f"/api/conversations/{conv_id}", headers=emp["headers"]
    ).json()["data"]
    assert "员工考勤与休假管理制度.md" not in after["messages"][1]["content"], (
        "旧形状清单消息绕过了脱敏 —— 回退读取没生效"
    )
    assert after["messages"][1]["meta"]["redacted"] is True


# ==================== 流式（SSE） ====================


def _parse_sse(resp) -> list[tuple[str, dict]]:
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


def test_stream_list_mode(client, make_user, agent, session_factory, monkeypatch):
    """SSE 路径的清单：节点是 list_docs、meta 的 answer_mode=catalog、照常落库。"""
    monkeypatch.setattr(db_session, "new_session", session_factory)

    admin = make_user("admin")
    kb_id = _seed(session_factory, admin["id"], "员工手册.md")

    with client.stream(
        "POST", "/api/agent/chat/stream",
        json={"query": "有哪些文档"}, headers=admin["headers"],
    ) as resp:
        assert resp.status_code == 200
        frames = _parse_sse(resp)

    steps = [d for ev, d in frames if ev == "step"]
    assert [s["node"] for s in steps] == ["analyze", "list_docs"]
    metas = [d for ev, d in frames if ev == "meta"]
    assert metas[0]["answer_mode"] == "catalog"
    done = frames[-1][1]
    assert "员工手册.md" in done["answer"]
    assert done["saved"] is True

    # 流式落库的 meta 也要记下"清单涉及哪个库"（撤权脱敏靠它；非流式另有专条）——
    # 两处 meta 组装是分开写的代码，漏一处就漏一半。
    detail = client.get(
        f"/api/conversations/{done['conversation_id']}", headers=admin["headers"]
    ).json()["data"]
    assert detail["messages"][1]["meta"]["catalog_kb_ids"] == [kb_id]
