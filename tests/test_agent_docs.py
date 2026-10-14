"""Agent 文档任务（fetch_docs + get_document）的测试。

Phase 15：analyze 从"要不要查库"扩成三值（SEARCH / DOCS / SKIP），
新增 `fetch_docs` 节点与 `get_document` 工具。这里盯的是这次扩展的**工程行为**：

  · 路由分叉对不对（文档任务不过 evaluate、检索路径不受影响）；
  · **匹配不上会不会静默失败**（回落检索是设计的核心保险，必须真发生）；
  · 权限有没有穿透（员工不能靠"猜文档名"看到无权库的文档）；
  · 对比场景两份文档会不会互相抢预算（第一份吃光、第二份被截成空 = 静默错答案）；
  · `_parse_analyze` 的格式容错（模型输出不听话时不能让整条链断掉）。

两条防线分开测：
  · 图的上层行为 —— mock 掉 LLM 与两个工具，看"怎么走"；
  · 工具本身 —— 直接连真表（SQLite），看"匹配得对不对"。
"""

import uuid

import pytest

from app.agent import graph as graph_mod
from app.agent import tools as tools_mod
from app.config import settings
from app.models import (
    STATUS_COMPLETED,
    Document,
    DocumentChunk,
    KnowledgeBase,
    User,
)


def _out(text: str) -> dict:
    return {
        "answer": text,
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "finish_reason": "stop",
    }


def _hit(chunk_id: int = 1, name: str = "员工手册.txt",
         content: str = "员工入职满一年可享受带薪年假 5 天。") -> dict:
    """检索命中的通用块（与 test_agent.py 同构）。"""
    return {
        "chunk_id": chunk_id,
        "document_id": 1,
        "document_name": name,
        "knowledge_base_id": 1,
        "page_number": None,
        "score": 0.9,
        "content": content,
    }


def _docitem(chunk_id: int, doc_id: int, name: str, content: str, kb: int = 1) -> dict:
    """文档任务的全文块（score=None —— 文档级命中没有"相似度"这个概念）。"""
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "document_name": name,
        "knowledge_base_id": kb,
        "page_number": None,
        "score": None,
        "content": content,
    }


@pytest.fixture
def agent(monkeypatch):
    """替掉 LLM 与**两个**取数工具，并把调用记录暴露给用例。"""
    box: dict = {
        "analyze": "SEARCH",
        "docs": {},           # 文档关键词 → get_document 的 results
        "chat_calls": [],
        "searches": [],
        "doc_calls": [],
    }

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        box["chat_calls"].append({"prompt": system[:24], "user": user[:60]})
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out(box["analyze"])
        if system == graph_mod.EVALUATE_SYSTEM:
            return _out("SUFFICIENT")
        if system == graph_mod.DIRECT_SYSTEM:
            return _out("你好，我是企业知识库助手。")
        return _out("对比答案 [1][2]。")

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        box["searches"].append({"query": query})
        return {
            "results": [_hit()],
            "searched_kb_ids": kb_ids or [1],
            "reranked": False,
            "skipped_orphans": 0,
        }

    def fake_get_document(db, user, name, kb_ids=None):
        box["doc_calls"].append({"name": name})
        results = box["docs"].get(name, [])
        return {"results": results, "searched_kb_ids": kb_ids or [1], "matched": len(results)}

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    monkeypatch.setattr(graph_mod.tools, "search_knowledge", fake_search)
    monkeypatch.setattr(graph_mod.tools, "get_document", fake_get_document)
    return box


def _ask(client, admin, query="对比一下考勤和报销制度"):
    return client.post("/api/agent/chat", json={"query": query}, headers=admin["headers"]).json()


# ==================== 图的上层行为 ====================


def test_docs_mode_fetches_full_text_and_skips_evaluate(client, make_user, agent):
    """DOCS 模式：取两份文档全文 → 直接生成（不过 evaluate）。"""
    agent["analyze"] = "DOCS: 考勤 | 报销"
    agent["docs"] = {
        "考勤": [_docitem(101, 11, "员工考勤与休假管理制度.md", "年假满一年 5 天。")],
        "报销": [_docitem(102, 12, "费用报销管理制度.md", "住宿标准 900 元。")],
    }
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    assert data["hit"] is True
    # 路由：analyze → fetch_docs → generate（没有 evaluate）
    assert [s["node"] for s in data["steps"]] == ["analyze", "fetch_docs", "generate"]
    # 两次取数按 analyze 点名的顺序
    assert [c["name"] for c in agent["doc_calls"]] == ["考勤", "报销"]
    assert agent["searches"] == [], "文档任务不该走检索"
    # 轨迹里能看出用了哪个工具、匹配了几份
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["tool"] == "get_document"
    assert fd["matched"] == 2
    # sources：两份文档，且 **score 为 None**（文档级命中没有相似度）
    assert [s["document_name"] for s in data["sources"]] == [
        "员工考勤与休假管理制度.md", "费用报销管理制度.md",
    ]
    assert all(s["score"] is None for s in data["sources"])
    assert data["answer_mode"] == "grounded"


def test_unmatched_docs_fall_back_to_search(client, make_user, agent):
    """一份都没匹配到 → **回落检索**，而不是静默失败。

    这是整个设计的保险丝：模型多判了 DOCS 的代价必须约等于零，
    否则"让模型大胆判"这个前提就不成立。
    """
    agent["analyze"] = "DOCS: 不存在的词"
    agent["docs"] = {}
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    assert len(agent["searches"]) == 1, "回落检索没有发生"
    assert [s["node"] for s in data["steps"]] == ["analyze", "fetch_docs", "generate"]
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["matched"] == 0
    assert fd.get("fallback") == "search", "轨迹里必须留下回落痕迹"
    assert data["hit"] is True  # 回落检索有结果，正常作答


def test_doc_task_word_bypasses_business_rule(client, make_user, agent):
    """「对比考勤和报销制度」命中业务词"考勤"，但含文档任务词 → **不短路**，交给模型判。

    短路的后果：既不会进 DOCS 分支（功能静默失效），也不会多花那一次调用 ——
    所以这里钉的是"必须问模型"。
    """
    agent["analyze"] = "DOCS: 考勤 | 报销"
    agent["docs"] = {
        "考勤": [_docitem(101, 11, "员工考勤与休假管理制度.md", "年假 5 天。")],
        "报销": [_docitem(102, 12, "费用报销管理制度.md", "住宿 900 元。")],
    }
    admin = make_user("admin")
    _ask(client, admin, query="对比考勤和报销制度")

    assert any(
        c["prompt"].startswith(graph_mod.ANALYZE_SYSTEM[:14]) for c in agent["chat_calls"]
    ), "含文档任务词时被规则短路了，没问模型"


def test_pure_business_question_still_skips_analyze_llm(client, make_user, agent):
    """对照组：不含文档任务词的业务问题**照旧走规则短路**（不花那次模型调用）。"""
    admin = make_user("admin")
    _ask(client, admin, query="年假几天")

    assert not any(
        c["prompt"].startswith(graph_mod.ANALYZE_SYSTEM[:14]) for c in agent["chat_calls"]
    ), "普通业务问题被问了模型，规则先手失效"


def test_docs_budget_split_between_documents(client, make_user, agent, monkeypatch):
    """对比场景：两份超长文档要对半分预算，**不能第一份吃光、第二份被截成空**。"""
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHARS", 2000)  # 每份预算 1000
    agent["analyze"] = "DOCS: 甲 | 乙"
    agent["docs"] = {
        "甲": [_docitem(1, 11, "甲.md", "A" * 3000)],
        "乙": [_docitem(2, 12, "乙.md", "B" * 3000)],
    }
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    assert [s["document_name"] for s in data["sources"]] == ["甲.md", "乙.md"], "第二份被饿死了"
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["truncated"] == 2, "两份都超预算，都该被标记截断"


# ==================== _parse_analyze 的格式容错 ====================


def test_parse_analyze_formats():
    """模型输出不听话时的容错 —— 任何解析不出的形状都默认回检索，不许空转。"""
    assert graph_mod._parse_analyze("SEARCH") == ("search", [])
    assert graph_mod._parse_analyze("SKIP") == ("skip", [])
    assert graph_mod._parse_analyze("") == ("search", [])
    assert graph_mod._parse_analyze("NO，这是寒暄") == ("skip", [])
    # 旧的 NEED 输出（万一模型记旧版提示词）→ 照样当检索
    assert graph_mod._parse_analyze("NEED") == ("search", [])
    # DOCS：中英文冒号、多种分隔符
    assert graph_mod._parse_analyze("DOCS: 考勤 | 报销") == ("docs", ["考勤", "报销"])
    assert graph_mod._parse_analyze("docs：考勤、报销制度") == ("docs", ["考勤", "报销制度"])
    assert graph_mod._parse_analyze("DOC: A，B / C") == ("docs", ["A", "B", "C"])
    # 说了 DOCS 却没给名字 → 当检索处理（不空转）
    assert graph_mod._parse_analyze("DOCS:") == ("search", [])
    # 超过 3 份 → 截断（成本闸门）
    assert graph_mod._parse_analyze("DOCS: a|b|c|d") == ("docs", ["a", "b", "c"])


# ==================== get_document 工具本身（真表） ====================


def _seed_doc(session_factory, owner_id, filename, contents):
    """造「知识库 → 文档 → 多块」。返回 (kb_id, doc_id, [chunk_id...])。"""
    with session_factory() as db:
        kb = KnowledgeBase(
            name="kb-" + uuid.uuid4().hex[:10], description="测试", owner_id=owner_id
        )
        db.add(kb)
        db.flush()
        doc = Document(
            knowledge_base_id=kb.id,
            uploader_id=owner_id,
            filename=filename,
            stored_name=uuid.uuid4().hex + ".md",
            file_path="/tmp/x.md",
            file_type="md",
            file_size=100,
            status=STATUS_COMPLETED,
            chunk_count=len(contents),
            char_count=sum(len(c) for c in contents),
        )
        db.add(doc)
        db.flush()
        chunk_ids = []
        for i, text in enumerate(contents):
            ch = DocumentChunk(
                document_id=doc.id, chunk_index=i, content=text, char_count=len(text)
            )
            db.add(ch)
            db.flush()
            chunk_ids.append(ch.id)
        db.commit()
        return kb.id, doc.id, chunk_ids


def test_get_document_matches_and_strips_suffix(make_user, session_factory):
    """匹配：全名子串 / 剥尾缀 / 多块按序拼接 / 取不到返回空。"""
    admin = make_user("admin")
    _, doc_id, chunk_ids = _seed_doc(
        session_factory, admin["id"], "员工考勤与休假管理制度.md",
        ["年假满一年 5 天。", "满三年 10 天。"],
    )
    with session_factory() as db:
        user = db.get(User, admin["id"])

        r = tools_mod.get_document(db, user, "员工考勤", kb_ids=None)
        assert r["matched"] == 1
        item = r["results"][0]
        assert item["document_name"] == "员工考勤与休假管理制度.md"
        assert item["document_id"] == doc_id
        # 全文 = 所有块按 chunk_index 顺序拼接
        assert item["content"] == "年假满一年 5 天。\n\n满三年 10 天。"
        assert item["chunk_id"] == chunk_ids[0], "chunk_id 取首块（溯源入口）"
        assert item["score"] is None, "文档级命中不许编一个相似度"

        # 剥尾缀："考勤制度" → "考勤"
        r2 = tools_mod.get_document(db, user, "考勤制度", kb_ids=None)
        assert r2["matched"] == 1

        # 取不到 → 空（由调用方回落）
        r3 = tools_mod.get_document(db, user, "销售手册", kb_ids=None)
        assert r3["matched"] == 0
        assert r3["results"] == []


def test_get_document_respects_permission_and_request_scope(make_user, session_factory):
    """权限两条：无可见库的员工匹配不到；管理员也能被请求里的 kb_ids 收窄。"""
    admin, emp = make_user("admin"), make_user()
    kb_id, _doc_id, _chunks = _seed_doc(
        session_factory, admin["id"], "考勤制度.md", ["年假 5 天。"]
    )
    with session_factory() as db:
        # 员工：该库没授权给他（也不是公开库）→ 什么都匹配不到
        emp_user = db.get(User, emp["id"])
        assert tools_mod.get_document(db, emp_user, "考勤", kb_ids=None)["matched"] == 0, (
            "员工靠猜文档名看到了无权库的文档"
        )

        # 管理员：默认能匹配到
        admin_user = db.get(User, admin["id"])
        assert tools_mod.get_document(db, admin_user, "考勤", kb_ids=None)["matched"] == 1
        # 但请求把范围限定到别的库（999）→ 必须收窄
        assert tools_mod.get_document(db, admin_user, "考勤", kb_ids=[999])["matched"] == 0
        # 限定到正确的库 → 又能匹配
        assert tools_mod.get_document(db, admin_user, "考勤", kb_ids=[kb_id])["matched"] == 1


def test_get_document_only_reads_chunks_of_that_document(make_user, session_factory):
    """只取点名文档的块 —— 别把库里别的文档混进来（对比任务最怕串味）。"""
    admin = make_user("admin")
    _seed_doc(session_factory, admin["id"], "考勤制度.md", ["年假 5 天。"])
    _seed_doc(session_factory, admin["id"], "报销制度.md", ["住宿 900 元。"])
    with session_factory() as db:
        user = db.get(User, admin["id"])
        r = tools_mod.get_document(db, user, "考勤", kb_ids=None)
        assert r["matched"] == 1
        assert "住宿" not in r["results"][0]["content"]
