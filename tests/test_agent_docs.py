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
        "candidates": {},     # 文档关键词 → 候选数（同名歧义场景用）
        "search_results": None,  # 补检索/回落检索返回什么（None = 默认一条命中）
        "chat_calls": [],
        "searches": [],
        "doc_calls": [],
    }

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        box["chat_calls"].append(
            {"prompt": system[:24], "user": user[:60], "user_full": user}
        )
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out(box["analyze"])
        if system == graph_mod.EVALUATE_SYSTEM:
            return _out("SUFFICIENT")
        if system == graph_mod.DIRECT_SYSTEM:
            return _out("你好，我是企业知识库助手。")
        return _out("对比答案 [1][2]。")

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        box["searches"].append({"query": query})
        results = (
            box["search_results"] if box["search_results"] is not None else [_hit()]
        )
        return {
            "results": results,
            "searched_kb_ids": kb_ids or [1],
            "reranked": False,
            "skipped_orphans": 0,
        }

    def fake_get_document(db, user, name, kb_ids=None):
        box["doc_calls"].append({"name": name})
        results = box["docs"].get(name, [])
        return {
            "results": results,
            "searched_kb_ids": kb_ids or [1],
            "matched": len(results),
            "candidates": box["candidates"].get(name, len(results)),
        }

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


def test_unmatched_name_gets_supplementary_search(client, make_user, agent):
    """名字没匹配到整篇 → **按那个名字补一次检索**，不静默失败。

    这是整个设计的保险丝：模型多判了 DOCS 的代价必须约等于零，
    否则"让模型大胆判"这个前提就不成立。
    「补检索」比旧版的「直接按原句回落」更精准 —— 缺的是"报销"，
    就该按"报销"去找相关内容，而不是把整句话再检索一遍。
    """
    agent["analyze"] = "DOCS: 不存在的词"
    agent["docs"] = {}
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    # 补检索用的是**缺失的名字**
    assert [s["query"] for s in agent["searches"]] == ["不存在的词"]
    assert [s["node"] for s in data["steps"]] == ["analyze", "fetch_docs", "generate"]
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["matched"] == 0
    assert fd["missed"] == "不存在的词", "未命中的名字必须留在轨迹里"
    # 补检索的片段进了引用 —— 不是"静默失败"
    assert [s["document_name"] for s in data["sources"]] == ["员工手册.txt"]
    assert data["hit"] is True  # 有内容，正常作答


def test_all_empty_falls_back_to_query_search(client, make_user, agent):
    """名字没命中 + **补检索也空** → 才回落按原句检索（最后的保底，轨迹留痕）。

    这是"模型多判 DOCS 代价≈0"的最后一块：无论前面怎么落空，
    都还有一次普通检索垫底 —— 退化成"就是慢了一点的纯 RAG"，而不是"什么都答不出"。
    """
    agent["analyze"] = "DOCS: 不存在的词"
    agent["docs"] = {}
    agent["search_results"] = []  # 补检索与回落检索都空
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    # 先按名字补检索，落空后回落按**原句**检索
    assert [s["query"] for s in agent["searches"]] == [
        "不存在的词", "对比一下考勤和报销制度",
    ]
    assert [s["node"] for s in data["steps"]] == ["analyze", "fetch_docs", "generate"]
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["matched"] == 0
    assert fd.get("fallback") == "search", "轨迹里必须留下回落痕迹"
    assert data["hit"] is False  # 都没内容 → 与纯 RAG 同口径的"无资料"


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
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHARS", 2000)  # 每份预算 (2000-120)//2
    agent["analyze"] = "DOCS: 甲 | 乙"
    agent["docs"] = {
        "甲": [_docitem(1, 11, "甲.md", "A" * 3000)],
        "乙": [_docitem(2, 12, "乙.md", "B" * 3000)],
    }
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    assert [s["document_name"] for s in data["sources"]] == ["甲.md", "乙.md"], "第二份被饿死了"
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    # 截断要记**名字**（只记个数的话，现场查不出"是哪份被截了"；用户也无从得知）
    assert fd["truncated"] == "甲.md | 乙.md", "两份都超预算，都该按名标出"


def test_partial_match_supplements_the_missing_half(client, make_user, agent):
    """🔴 只有一半命中时**不能只答一半** —— 缺的那半边补检索，且如实告知。

    错在哪（评审实测）：DOCS 甲|乙、只有甲命中时，旧实现直接返回"只有甲"的答案，
    界面毫无异常 —— 用户以为对比是全的，实际缺半边。**自信的缺一半比答不出来更坏**。
    """
    agent["analyze"] = "DOCS: 甲 | 乙"
    agent["docs"] = {"甲": [_docitem(1, 11, "甲.md", "甲的全部内容。")]}
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    # 乙走了补检索（用"乙"做检索词）
    assert [s["query"] for s in agent["searches"]] == ["乙"]
    # 两条引用都在：甲（整篇，无相似度）+ 乙（检索片段，有相似度）
    assert [s["document_name"] for s in data["sources"]] == ["甲.md", "员工手册.txt"]
    assert data["sources"][0]["score"] is None
    assert data["sources"][1]["score"] is not None
    # 轨迹里记了未命中的名字
    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    assert fd["missed"] == "乙"
    assert fd["matched"] == 1  # matched 只数"取到整篇"的
    # 🔴 缺口说明进了生成提示词 —— 否则答案看起来是完整的
    prompts = [c["user_full"] for c in agent["chat_calls"]]
    assert any("没有匹配到" in p and "乙" in p for p in prompts), (
        "缺口没有告知生成节点，它会写出看似完整的半边对比"
    )


def test_budget_overflow_takes_fewer_docs_and_stays_within_total(
    client, make_user, agent, monkeypatch
):
    """🔴 配到下限（500 字）+ 3 份点名 → **少取几份**，总量绝不超预算。

    错在哪（评审实测）：旧实现 `max(200, 平摊)` 的单份下限会推翻"总量 ≤ 总预算"的
    不变量 —— P=500、3 份时每份 200、总量 600+，第三份被下游 build_context 静默丢掉，
    模型还会写出指不到来源的 [3]。这正是"分预算"当初要防的问题，只是换了触发条件。

    这条例的判据是**不变量本身**：进生成提示词的上下文总量必须 ≤ RAG_MAX_CONTEXT_CHARS。
    """
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHARS", 500)
    agent["analyze"] = "DOCS: 甲 | 乙 | 丙"
    agent["docs"] = {
        "甲": [_docitem(1, 11, "甲.md", "A" * 3000)],
        "乙": [_docitem(2, 12, "乙.md", "B" * 3000)],
        "丙": [_docitem(3, 13, "丙.md", "C" * 3000)],
    }
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    fd = next(s for s in data["steps"] if s["node"] == "fetch_docs")
    # 只取 1 份整篇（capacity=320 撑不住 3×(400+60)），其余降级为检索片段
    assert fd["matched"] == 1
    assert fd["dropped"] == "乙 | 丙", "被裁的名字必须留在轨迹里"
    # 三条引用都在：没有谁能被静默丢掉（对比的必要条件）
    assert len(data["sources"]) == 3

    # 🔴 不变量：进提示词的上下文（含标题行）≤ 总预算
    gen_prompt = next(c["user_full"] for c in agent["chat_calls"] if "参考资料：" in c["user_full"])
    context = gen_prompt.split("参考资料：\n", 1)[1].split("\n\n问题：", 1)[0]
    assert len(context) <= 500, f"上下文 {len(context)} 字，超过预算 500 —— 下游会静默丢块"


def test_iterations_and_fetched_are_separate(client, make_user, agent):
    """文档任务 iterations=0（没检索），但 searched_kb_ids 必须保留（取过数）。

    错在哪：两者曾共用一个字段 —— 界面把文档任务显示成"检索轮数：1"
    （它一次检索都没做），或者反过来把取数范围标成"没查任何库"。
    """
    agent["analyze"] = "DOCS: 考勤"
    agent["docs"] = {"考勤": [_docitem(1, 11, "考勤.md", "年假 5 天。")]}
    admin = make_user("admin")
    data = _ask(client, admin)["data"]

    assert data["iterations"] == 0, "文档任务没有检索轮数可言"
    assert data["searched_kb_ids"] == [1], "取过数，范围不能丢（丢了会显示成'没查任何库'）"


def test_doc_notes_absent_when_everything_is_complete(client, make_user, agent):
    """对照组：全部命中的正常对比，**不许**冒出"缺口说明"（不能把谎话喂给模型）。"""
    agent["analyze"] = "DOCS: 甲 | 乙"
    agent["docs"] = {
        "甲": [_docitem(1, 11, "甲.md", "甲的内容。")],
        "乙": [_docitem(2, 12, "乙.md", "乙的内容。")],
    }
    admin = make_user("admin")
    _ask(client, admin)

    prompts = [c["user_full"] for c in agent["chat_calls"]]
    assert not any("系统补充" in p for p in prompts), "完整取数却报了缺口，会误导模型"


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


def _seed_doc_row(session_factory, owner_id, filename, contents, status=STATUS_COMPLETED):
    """同 `_seed_doc`，但可以指定 status（默认 completed）。"""
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
            status=status,
            chunk_count=len(contents),
            char_count=sum(len(c) for c in contents),
        )
        db.add(doc)
        db.flush()
        for i, text in enumerate(contents):
            db.add(DocumentChunk(
                document_id=doc.id, chunk_index=i, content=text, char_count=len(text),
            ))
        db.commit()
        return kb.id, doc.id


def test_processing_document_is_not_matched(make_user, session_factory):
    """🔴 只取 **completed** 的文档 —— 重跑处理中的文档命中的是上一轮旧切块。

    错在哪（评审实测）：`_match_document` 不过滤状态，能命中 processing / failed
    的文档；而"重跑向量化"期间新旧切块并存，拿去"总结"会总结出**旧内容**。
    检索侧（vector_service）本来就要求 completed —— 两边口径必须一致，
    否则会出现"检索搜不到、文档任务却取到了"的偏差。
    """
    from app.models.document import STATUS_PROCESSING

    admin = make_user("admin")
    _seed_doc_row(session_factory, admin["id"], "考勤制度.md", ["旧的一版：年假 3 天。"],
                  status=STATUS_PROCESSING)
    with session_factory() as db:
        user = db.get(User, admin["id"])
        r = tools_mod.get_document(db, user, "考勤", kb_ids=None)
        assert r["matched"] == 0, "处理中的文档被取到了 —— 会总结出上一轮的旧内容"


def test_same_length_filenames_prefer_newest(make_user, session_factory):
    """同名（同长度）多份 → 取**最新上传**的，并把候选数带出来。

    错在哪（评审实测）：旧排序 `length, id` 在同长度时永远选**先建的** ——
    《管理制度汇编(2024)》和《(2025)》之间选中 2024，用户拿到旧版还不知道，
    因为对外只报 matched=1、没有任何"有两份"的迹象。
    """
    admin = make_user("admin")
    _kb1, old_id = _seed_doc_row(session_factory, admin["id"], "制度汇编(2024).md", ["旧版内容。"])
    _kb2, new_id = _seed_doc_row(session_factory, admin["id"], "制度汇编(2025).md", ["新版内容。"])
    with session_factory() as db:
        user = db.get(User, admin["id"])
        r = tools_mod.get_document(db, user, "制度汇编", kb_ids=None)
        assert r["matched"] == 1
        assert r["results"][0]["document_id"] == new_id, "同长度时取了旧版本"
        assert r["candidates"] >= 2, "有两份候选却不告诉调用方（写进轨迹才有机会被发现）"


def test_suffix_stripping_loops_until_match(make_user, session_factory):
    """尾缀要**逐级剥、逐级试**："考勤管理制度" → "考勤管理" → "考勤" 才命中。

    错在哪（评审实测）：只剥一层时 "考勤管理制度" → "考勤管理"，
    而《员工考勤与休假管理制度》里没有"考勤管理"这个子串 → 匹配失败。
    这正是注释里举的那类"人话"（用户不会完整念出文件名）。
    """
    admin = make_user("admin")
    _seed_doc(session_factory, admin["id"], "员工考勤与休假管理制度.md", ["年假 5 天。"])
    with session_factory() as db:
        user = db.get(User, admin["id"])
        r = tools_mod.get_document(db, user, "考勤管理制度", kb_ids=None)
        assert r["matched"] == 1, "只剥一层尾缀，'考勤管理制度' 匹配不到《员工考勤与休假管理制度》"
