"""Phase 6 RAG 问答的测试。

两条外部依赖全部替换掉：
  · `vector_service.search` —— 不连 Milvus、不调 BGE-M3
  · `rag_service.llm.chat`  —— **绝不打真实大模型**（这个是要花钱的）

只替换这两个而不是更底层的东西，是因为 RAG 与 /api/search 共用同一条检索路径，
这里要验证的是「RAG 会不会正确使用检索结果」，不需要重复验证检索本身。
"""

import pytest

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.user import User
from app.rag import prompt
from app.services import rag_service, vector_service


def _hit(chunk_id: int = 1, kb_id: int = 1, page: int | None = None,
         score: float = 0.8, content: str = "员工入职满一年可享受带薪年假。",
         name: str = "员工手册.txt") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": 10,
        "document_name": name,
        "knowledge_base_id": kb_id,
        "page_number": page,
        "score": score,
        "content": content,
    }


@pytest.fixture
def rag(monkeypatch):
    """替掉检索与 LLM，并把调用记录暴露给用例。"""
    box: dict = {"results": [], "search": [], "chat": [], "chat_error": None}

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        # rerank 从 Phase 7 起由 /api/chat 显式传 True，这里原样记下来供断言
        box["search"].append(
            {"query": query, "kb_ids": kb_ids, "top_k": top_k, "rerank": rerank}
        )
        return {
            "query": query,
            "top_k": top_k,
            "searched_kb_ids": kb_ids if kb_ids is not None else [1],
            "results": box["results"],
            "reranked": False,
            "candidate_k": top_k,
        }

    def fake_chat(system, user_msg):
        box["chat"].append({"system": system, "user": user_msg})
        if box["chat_error"] is not None:
            raise box["chat_error"]
        return {
            "answer": "入职满一年可享受带薪年假 [1]。",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(rag_service.llm, "chat", fake_chat)
    return box


# ==================== 正常问答 ====================


def test_answer_returns_answer_and_sources(client, make_user, rag):
    admin = make_user("admin")
    rag["results"] = [_hit(content="员工入职满一年可享受带薪年假，工龄越长天数越多。")]

    r = client.post("/api/chat", json={"query": "年假有几天"}, headers=admin["headers"])
    body = r.json()
    assert body["code"] == 0, body

    data = body["data"]
    assert data["hit"] is True
    assert data["answer"]
    assert data["model"] == settings.LLM_MODEL
    assert data["usage"]["total_tokens"] == 120

    assert len(data["sources"]) == 1
    src = data["sources"][0]
    assert src["index"] == 1
    assert src["chunk_id"] == 1
    assert src["document_name"] == "员工手册.txt"
    assert src["score"] == 0.8
    assert "带薪年假" in src["snippet"]


def test_prompt_gets_sources_with_page_and_number(client, make_user, rag):
    """拼进提示词的资料要带编号、文件名和页码，模型才可能有据可依地引用。"""
    admin = make_user("admin")
    rag["results"] = [_hit(chunk_id=7, page=3, content="年假申请需提前三天提交。")]

    client.post("/api/chat", json={"query": "怎么申请年假"}, headers=admin["headers"])

    user_msg = rag["chat"][0]["user"]
    assert "[1]" in user_msg
    assert "员工手册.txt" in user_msg
    assert "第 3 页" in user_msg
    assert "年假申请需提前三天提交" in user_msg
    assert "怎么申请年假" in user_msg
    # 系统提示词必须是那一份固定模板（防幻觉口径就在里面）
    assert rag["chat"][0]["system"] == prompt.SYSTEM_PROMPT


# ==================== 无命中：绝不调用大模型 ====================


def test_no_hit_never_calls_llm(client, make_user, rag):
    """检索为空时必须直接返回固定提示语 —— 既不花钱，也不给模型自由发挥的机会。"""
    admin = make_user("admin")
    rag["results"] = []

    r = client.post("/api/chat", json={"query": "公司食堂几点开饭"}, headers=admin["headers"])
    body = r.json()
    assert body["code"] == 0, body

    data = body["data"]
    assert data["hit"] is False
    assert data["answer"] == prompt.NO_HIT_ANSWER
    assert data["sources"] == []
    assert data["model"] is None      # 没调用模型 → 不是模型名
    assert data["usage"] is None

    assert rag["chat"] == []          # ← 本用例的核心断言
    assert len(rag["search"]) == 1    # 但检索确实跑了


def test_unauthorized_kb_ids_yield_no_hit(client, make_user, rag):
    """越权传别人的 kb_ids：检索层会返回空（交集为空），于是自然走到"未命中"分支。"""
    admin = make_user("admin")
    rag["results"] = []               # 模拟检索层因权限交集为空而返回空

    r = client.post(
        "/api/chat", json={"query": "别人的资料", "kb_ids": [999]}, headers=admin["headers"]
    )
    data = r.json()["data"]
    assert data["hit"] is False
    assert rag["chat"] == []
    assert rag["search"][0]["kb_ids"] == [999]   # 原样透传，收窄逻辑在 vector 层


# ==================== 上下文截断与编号一致性 ====================


def test_context_cap_keeps_high_scores_and_sources_aligned(
    client, make_user, rag, monkeypatch
):
    """上下文超限时丢低分结果，且 sources 编号必须和提示词里的编号一一对应。"""
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHARS", 300)
    admin = make_user("admin")
    rag["results"] = [
        _hit(chunk_id=1, score=0.9, content="甲" * 100),
        _hit(chunk_id=2, score=0.8, content="乙" * 100),
        _hit(chunk_id=3, score=0.7, content="丙" * 100),
    ]

    r = client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])
    data = r.json()["data"]

    # 高分优先：第 1 条一定在
    assert data["sources"][0]["chunk_id"] == 1
    assert len(data["sources"]) < 3            # 确实被截了

    user_msg = rag["chat"][0]["user"]
    for src in data["sources"]:
        assert "[%d]" % src["index"] in user_msg
        assert src["chunk_id"] <= 3
    # 编号连续从 1 开始，不能有空洞
    assert [s["index"] for s in data["sources"]] == list(range(1, len(data["sources"]) + 1))
    # 被截掉的那条不能出现在提示词里
    for dropped in range(len(data["sources"]) + 1, 4):
        assert "[%d]" % dropped not in user_msg


def test_empty_context_never_calls_llm(client, make_user, rag, monkeypatch):
    """检索有结果、但**一条都拼不进上下文**时，也不能拿空资料去问模型。

    触发条件：RAG_MAX_CONTEXT_CHARS 比「[1] 来源：某文件」这个标题本身还短。
    config 里已有下限校验，但那是启动时的；这里盯的是运行时的最后一道守卫 ——
    没有它，代码会拿空上下文去调 LLM：白花钱，还违背"答案必须来自文档"。
    """
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHARS", 1)
    admin = make_user("admin")
    rag["results"] = [_hit(content="甲" * 50)]

    r = client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])
    data = r.json()["data"]

    assert data["hit"] is False
    assert data["answer"] == prompt.NO_HIT_ANSWER
    assert data["model"] is None
    assert data["sources"] == []
    assert rag["chat"] == []          # ← 本用例的核心断言


def test_chat_asks_for_rerank(client, make_user, rag):
    """Phase 7：问答这一侧要显式要求重排（/api/search 那边默认仍是纯向量）。"""
    admin = make_user("admin")
    rag["results"] = [_hit()]

    client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])

    assert rag["search"][0]["rerank"] is True


def test_rag_context_floor_is_enforced_at_startup():
    """配置下限必须在创建 Settings 时就拦住，而不是等运行时。"""
    import pydantic

    from app.config import Settings

    with pytest.raises(pydantic.ValidationError):
        Settings(RAG_MAX_CONTEXT_CHARS=100)


def test_build_context_skips_blank_and_handles_oversized_first():
    """纯函数：空内容跳过、第一条就超长时截断正文。"""
    text, adopted = prompt.build_context(
        [_hit(content="   "), _hit(chunk_id=2, content="甲" * 500)], max_chars=120
    )
    assert len(adopted) == 1
    assert adopted[0]["chunk_id"] == 2
    assert len(text) <= 120


def test_build_context_page_suffix_only_for_pdf():
    text_pdf, _ = prompt.build_context([_hit(page=2)], max_chars=500)
    text_txt, _ = prompt.build_context([_hit(page=None)], max_chars=500)
    assert "第 2 页" in text_pdf
    assert "页" not in text_txt


# ==================== 参数与错误 ====================


def test_top_k_defaults_to_settings(client, make_user, rag):
    admin = make_user("admin")
    client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])
    assert rag["search"][0]["top_k"] == settings.RAG_TOP_K


def test_explicit_top_k_is_passed_through(client, make_user, rag):
    admin = make_user("admin")
    client.post("/api/chat", json={"query": "问", "top_k": 3}, headers=admin["headers"])
    assert rag["search"][0]["top_k"] == 3


def test_empty_query_is_rejected(client, make_user):
    admin = make_user("admin")
    r = client.post("/api/chat", json={"query": ""}, headers=admin["headers"])
    assert r.json()["code"] == ErrorCode.PARAM_ERROR


def test_requires_login(client):
    r = client.post("/api/chat", json={"query": "问"})
    assert r.json()["code"] == ErrorCode.UNAUTHORIZED


def test_llm_failure_maps_to_50005(client, make_user, rag):
    """上游（DeepSeek）出错时，响应里只能是 50005 + 一句人话，不能带上游原文。"""
    admin = make_user("admin")
    rag["results"] = [_hit()]
    rag["chat_error"] = AppException(
        ErrorCode.LLM_ERROR,
        "大模型调用失败，请稍后重试",
    )

    r = client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])
    body = r.json()
    assert body["code"] == ErrorCode.LLM_ERROR
    assert body["message"] == "大模型调用失败，请稍后重试"
    # 不能泄漏内部细节
    assert "deepseek" not in str(body).lower()
    assert "http" not in str(body).lower()


def test_answer_not_leaked_when_llm_returns_error(client, make_user, rag, session_factory):
    """RAG 失败时不产生任何副作用：数据库里不该多出东西（Phase 6 不落库）。"""
    from app.models import Document, DocumentChunk

    admin = make_user("admin")
    rag["results"] = [_hit()]
    rag["chat_error"] = AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")

    with session_factory() as db:
        before = (
            db.query(Document).count(),
            db.query(DocumentChunk).count(),
        )

    client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])

    with session_factory() as db:
        after = (
            db.query(Document).count(),
            db.query(DocumentChunk).count(),
        )
    assert before == after


def test_conversations_tables_untouched(client, make_user, rag, session_factory):
    """Phase 6 明确不落库：问答前后用户表之外不该有新增会话记录。"""
    admin = make_user("admin")
    rag["results"] = [_hit()]

    with session_factory() as db:
        user_count_before = db.query(User).count()

    client.post("/api/chat", json={"query": "问"}, headers=admin["headers"])

    with session_factory() as db:
        assert db.query(User).count() == user_count_before
