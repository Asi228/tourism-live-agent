"""Phase 8 Agent 的测试。

Agent 真正的风险不在"答得好不好"（那是模型的事），而在**工程行为**：
  会不会无限循环？闸门到顶了会不会收手？判定节点挂了会不会整个问答崩？
  没有资料时会不会照样调模型（白花钱）？走没走、走了几步，事后能不能查？

所以下面几乎每条用例都在盯这些。两条外部依赖全部替掉：LLM 与检索工具。
"""

import pytest

from app.agent import graph as graph_mod
from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode


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
    return {"answer": text, "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "finish_reason": "stop"}


@pytest.fixture
def agent(monkeypatch):
    """替掉 LLM 与检索工具，并把调用记录暴露给用例。"""
    box: dict = {
        "analyze": "NEED",
        "evaluate": "SUFFICIENT",
        "results": [_hit()],
        "results_by_call": None,   # 可选：按检索次序给不同结果（模拟"第一次没找到"）
        "search_scope": None,      # 可选：检索层返回的**真实**范围（与入参 kb_ids 不同）
        "chat_calls": [],
        "searches": [],
        "chat_error_on": None,     # 让某类节点抛异常，验证降级
    }

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        box["chat_calls"].append(
            {"prompt": system[:24], "user": user[:60], "max_tokens": max_tokens,
             "history": history}
        )
        if box["chat_error_on"] and box["chat_error_on"] in system:
            raise AppException(ErrorCode.LLM_ERROR, "大模型调用失败，请稍后重试")
        if system == graph_mod.ANALYZE_SYSTEM:
            return _out(box["analyze"])
        if system == graph_mod.EVALUATE_SYSTEM:
            return _out(box["evaluate"])
        if system == graph_mod.DIRECT_SYSTEM:
            return _out("你好，我是企业知识库助手，请提问与公司制度相关的问题。")
        return _out("入职满一年可享受带薪年假 5 天 [1]。")

    def fake_search(db, user, query, kb_ids=None, top_k=None):
        idx = len(box["searches"])
        box["searches"].append({"query": query, "kb_ids": kb_ids, "top_k": top_k})
        if box["results_by_call"] is not None:
            seq = box["results_by_call"]
            res = seq[min(idx, len(seq) - 1)]
        else:
            res = box["results"]
        scope = box["search_scope"] if box["search_scope"] is not None else (kb_ids or [1])
        return {"results": res, "searched_kb_ids": scope,
                "reranked": False, "skipped_orphans": 0}

    monkeypatch.setattr(graph_mod.llm, "chat", fake_chat)
    monkeypatch.setattr(graph_mod.tools, "search_knowledge", fake_search)
    return box


def _ask(client, admin, **payload):
    payload.setdefault("query", "年假几天")
    return client.post("/api/agent/chat", json=payload, headers=admin["headers"]).json()


# ==================== 基本链路 ====================


def test_normal_flow_returns_answer_and_trace(client, make_user, agent):
    admin = make_user("admin")
    body = _ask(client, admin)
    assert body["code"] == 0, body

    data = body["data"]
    assert data["hit"] is True
    assert "5 天" in data["answer"]
    assert data["iterations"] == 1
    assert [s["index"] for s in data["sources"]] == [1]
    # 轨迹必须完整可读：走了哪几个节点、每步多久
    assert [s["node"] for s in data["steps"]] == ["analyze", "retrieve", "evaluate", "generate"]
    assert all(isinstance(s["ms"], int) for s in data["steps"])
    assert data["elapsed_ms"] >= 0


def test_skip_retrieval_never_calls_search_tool(client, make_user, agent):
    """判定为"不需要查知识库"时，绝不能白跑一次检索。"""
    agent["analyze"] = "SKIP"
    admin = make_user("admin")

    body = _ask(client, admin, query="你好")
    data = body["data"]

    assert data["iterations"] == 0
    assert agent["searches"] == []
    assert data["hit"] is False
    assert [s["node"] for s in data["steps"]] == ["analyze", "generate"]
    # 直答走的是 DIRECT_SYSTEM，不是那份"只准依据资料作答"的提示词
    assert any(c["prompt"].startswith(graph_mod.DIRECT_SYSTEM[:20]) for c in agent["chat_calls"])


def test_business_word_forces_retrieval_without_calling_llm(client, make_user, agent):
    """业务疑问词命中 → 规则直接判 NEED，不问模型。

    为什么要有这道规则：analyze 判 SKIP 时走的是**全系统唯一允许模型在毫无资料时
    自由发挥**的路径（DIRECT_SYSTEM + temperature=0.5），而且那条路不经过 evaluate，
    没有任何机制纠正误判。所以业务问题用规则先手拦住，代价顶多多检索一次。
    """
    agent["analyze"] = "SKIP"      # 就算模型想偷懒说 SKIP，也不该被采纳
    admin = make_user("admin")

    data = _ask(client, admin, query="年假可以休几天")["data"]

    assert data["iterations"] == 1, "业务问题被当成寒暄，压根没去检索"
    first = data["steps"][0]
    assert first["node"] == "analyze"
    assert first.get("rule", "").startswith("business_word:")
    # 这次判定没花 token：根本没调模型
    assert not any(c["prompt"].startswith(graph_mod.ANALYZE_SYSTEM[:14]) for c in agent["chat_calls"])


def test_smalltalk_still_allowed_when_no_business_word(client, make_user, agent):
    """反向对照：不含业务词的寒暄仍要允许 SKIP —— 别把优化做成"永远去检索"。"""
    agent["analyze"] = "SKIP"
    admin = make_user("admin")

    data = _ask(client, admin, query="你好呀")["data"]

    assert data["iterations"] == 0
    assert agent["searches"] == []
    assert data["answer_mode"] == "smalltalk"


def test_searched_kb_ids_comes_from_retriever_not_request(client, make_user, agent):
    """🔴 必须是**检索层算出的真实范围**，不能回显请求参数 kb_ids。

    入参 kb_ids 只是"想缩小的范围"，没和用户权限求过交集；而本字段 None 的语义是
    「管理员不受限」—— 回显会把普通员工标成"扫全库"，前端展示和事后审计都会错。
    """
    agent["search_scope"] = [7]        # 检索层实际用的范围（与入参不同才测得出来）
    admin = make_user("admin")

    data = _ask(client, admin, kb_ids=[99, 100])["data"]

    assert data["searched_kb_ids"] == [7], "回显了请求参数，而不是真实检索范围"
    assert data["iterations"] == 1


def test_searched_kb_ids_is_empty_when_retrieval_skipped(client, make_user, agent):
    """"不需要检索"时给 []，不能是 None —— None 在本项目里表示「管理员不受限」。"""
    agent["analyze"] = "SKIP"
    admin = make_user("admin")

    data = _ask(client, admin, query="你好")["data"]

    assert data["iterations"] == 0
    assert data["searched_kb_ids"] == []


def test_answer_mode_distinguishes_three_ways(client, make_user, agent):
    """hit=false 有两种成因，靠 answer_mode 区分；命中资料时是 grounded。"""
    admin = make_user("admin")

    # ① 命中资料 → grounded
    assert _ask(client, admin)["data"]["answer_mode"] == "grounded"

    # ② 检索过但没资料 → no_evidence（hit 也是 false，但性质不同）
    agent["results"] = []
    data = _ask(client, admin)["data"]
    assert data["hit"] is False
    assert data["answer_mode"] == "no_evidence"

    # ③ 判定不需要查库 → smalltalk
    agent["analyze"] = "SKIP"
    data = _ask(client, admin, query="你好")["data"]
    assert data["hit"] is False
    assert data["answer_mode"] == "smalltalk"


def test_second_retrieval_when_material_is_insufficient(client, make_user, agent):
    """第一次资料不够 → evaluate 给出新关键词 → 再检索一次（这是 Agent 的核心价值）。"""
    agent["results_by_call"] = [[], [_hit(chunk_id=7)]]
    # 第一次检索是空的，judge 说不够并给个新词，才会走到第二次检索
    agent["evaluate"] = "RETRY: 年假 制度"
    admin = make_user("admin")

    body = _ask(client, admin)
    data = body["data"]

    assert data["iterations"] == 2
    assert len(agent["searches"]) == 2
    assert [s["node"] for s in data["steps"]].count("retrieve") == 2
    assert data["hit"] is True


def test_retry_query_comes_from_evaluate(client, make_user, agent):
    """第二次检索用的词应该来自 evaluate 的改写，而不是原样重试。"""
    agent["results_by_call"] = [[], [_hit()]]
    agent["evaluate"] = "RETRY: 年假 天数 规定"
    admin = make_user("admin")

    _ask(client, admin)

    assert agent["searches"][0]["query"] == "年假几天"
    assert agent["searches"][1]["query"] == "年假 天数 规定"


def test_stops_when_retrieval_adds_nothing(client, make_user, agent, monkeypatch):
    """🔴 重试之后仍然**一条新资料都没带来**时，立即收手。

    实测（Phase 8 端到端）：问知识库里没有的内容时，改写过的关键词召回的还是同样那几条，
    净增 0，却白跑了一轮判断 —— 同样的问题 tokens 从 303（纯 RAG）抬到 840。

    把上限放到 5，才能区分"因为无新增收手"和"因为到上限收手"——否则这个用例是假的。
    """
    monkeypatch.setattr(settings, "AGENT_MAX_ITERATIONS", 5)
    agent["results"] = [_hit(1), _hit(2)]      # 两轮返回完全相同 → 第二轮净增 0
    agent["evaluate"] = "RETRY: 换个词"
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["iterations"] == 2, "第二轮没有新增资料，不该继续重试"
    assert len(agent["searches"]) == 2
    assert data["answer"]


def test_keeps_retrying_while_new_material_appears(client, make_user, agent, monkeypatch):
    """反向对照：换词确实带来新资料时就要继续 —— 别把优化做成"永不重试"。"""
    monkeypatch.setattr(settings, "AGENT_MAX_ITERATIONS", 3)
    agent["results_by_call"] = [[_hit(1)], [_hit(1), _hit(2)], [_hit(1), _hit(2), _hit(3)]]
    agent["evaluate"] = "RETRY: 再换个词"
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["iterations"] == 3, "每轮都有新增，应当一直走到轮数上限"
    assert len(agent["searches"]) == 3


def test_iterations_capped_by_config(client, make_user, agent, monkeypatch):
    """🔴 成本硬闸门：模型一直说"不够"，也不能无限检索下去。"""
    monkeypatch.setattr(settings, "AGENT_MAX_ITERATIONS", 2)
    agent["results_by_call"] = [[_hit(1)], [_hit(2)], [_hit(3)], [_hit(4)]]
    agent["evaluate"] = "RETRY: 再试一次"
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["iterations"] == 2, "超过了 AGENT_MAX_ITERATIONS"
    assert len(agent["searches"]) == 2
    assert data["answer"]          # 到顶后必须仍然给出答案，而不是报错


def test_no_material_does_not_call_llm_for_answer(client, make_user, agent):
    """检索不到资料时，不拿空资料去问模型（与纯 RAG 同一口径）。"""
    from app.rag import prompt

    agent["results"] = []
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["hit"] is False
    assert data["answer"] == prompt.NO_HIT_ANSWER
    # 生成节点没被调用（只有 analyze + evaluate 那两次判定）
    generate_calls = [c for c in agent["chat_calls"] if c["prompt"].startswith("你是企业知识库的问答助手")]
    assert generate_calls == []


def test_empty_context_never_calls_llm(client, make_user, agent):
    """有命中、但一条都拼不进上下文（正文全是空白）→ 不能拿空资料去问模型。

    这条守卫原先只在纯 RAG 那一侧有，Agent 侧漏了 —— 两边都抽到
    `prompt.build_prompt_and_sources` 之后才对齐。用例钉住的是"抽完之后别再退化"。
    """
    from app.rag import prompt

    agent["results"] = [_hit(content="   \n  ")]
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["hit"] is False
    assert data["answer"] == prompt.NO_HIT_ANSWER
    assert data["answer_mode"] == "no_evidence"
    generate_calls = [
        c for c in agent["chat_calls"] if c["prompt"].startswith("你是企业知识库的问答助手")
    ]
    assert generate_calls == [], "拿空上下文调了模型（幻觉 + 白花钱）"


# ==================== 降级：判定节点挂了不能拖垮整个问答 ====================


def test_analyze_failure_falls_back_to_retrieval(client, make_user, agent):
    """判定节点挂了要降级，不能整个问答失败。

    注意这里刻意用一个**不含业务疑问词**的问题：含业务词的会被规则先手拦下
    （根本走不到模型），那样就测不到"模型调用失败"这条降级路径了。
    """
    agent["chat_error_on"] = graph_mod.ANALYZE_SYSTEM[:20]
    admin = make_user("admin")

    data = _ask(client, admin, query="帮我看看这个东西")["data"]

    assert data["iterations"] == 1, "判定失败后应当按「需要检索」继续"
    assert data["hit"] is True
    assert "判定失败" in str(data["steps"][0].get("decision", ""))


def test_evaluate_failure_treated_as_sufficient(client, make_user, agent):
    agent["chat_error_on"] = graph_mod.EVALUATE_SYSTEM[:20]
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["iterations"] == 1, "评估失败不该导致反复检索"
    assert data["answer"]


def test_retry_without_keyword_stops_instead_of_looping(client, make_user, agent):
    """说"不够"却给不出新关键词 → 再检索也是白跑，应当收手。"""
    agent["evaluate"] = "INSUFFICIENT"      # 没有 RETRY: 前缀
    admin = make_user("admin")

    data = _ask(client, admin)["data"]

    assert data["iterations"] == 1
    assert len(agent["searches"]) == 1


# ==================== 错误与开关 ====================


def test_switch_off_returns_40000(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_ENABLED", False)
    admin = make_user("admin")

    body = _ask(client, admin)

    assert body["code"] == ErrorCode.PARAM_ERROR
    assert "/api/chat" in body["message"]


def test_requires_login(client):
    r = client.post("/api/agent/chat", json={"query": "问"})
    assert r.json()["code"] == ErrorCode.UNAUTHORIZED


def test_empty_query_rejected(client, make_user):
    admin = make_user("admin")
    r = client.post("/api/agent/chat", json={"query": ""}, headers=admin["headers"])
    assert r.json()["code"] == ErrorCode.PARAM_ERROR


# ==================== 可观测性：agent_logs ====================


def test_agent_log_is_written(client, make_user, agent, session_factory):
    from app.models import AgentLog

    admin = make_user("admin")
    _ask(client, admin)

    with session_factory() as db:
        rows = db.query(AgentLog).filter(AgentLog.user_id == admin["id"]).all()

    assert len(rows) == 1
    row = rows[0]
    assert row.hit is True
    assert row.iterations == 1
    assert row.source_count == 1
    assert row.total_tokens > 0
    assert [s["node"] for s in row.steps] == ["analyze", "retrieve", "evaluate", "generate"]


def test_agent_log_records_failure(client, make_user, agent, session_factory, monkeypatch):
    """失败也要留痕：否则"这次为什么没答上来"事后完全查不到。"""
    from app.models import AgentLog
    from app.services import agent_service

    admin = make_user("admin")

    def boom(*a, **kw):
        raise RuntimeError("graph exploded")

    # 注意：agent_service 用的是 `from ... import build_graph`，名字绑在它自己的命名空间里，
    # 所以要 patch agent_service.build_graph —— patch 源模块（graph_mod）不会生效。
    monkeypatch.setattr(
        agent_service, "build_graph",
        lambda *a, **kw: type("G", (), {"invoke": staticmethod(boom)})(),
    )

    r = client.post("/api/agent/chat", json={"query": "问"}, headers=admin["headers"])
    assert r.json()["code"] == ErrorCode.LLM_ERROR

    with session_factory() as db:
        rows = db.query(AgentLog).filter(AgentLog.user_id == admin["id"]).all()
    assert len(rows) == 1
    assert rows[0].error_msg


def test_log_repository_swallows_db_error(session_factory, monkeypatch):
    """审计写入失败必须被吞掉并返回 None。

    它是日志、不是业务结果 —— 写不进去最多让"事后复盘"缺一条，
    绝不能让用户拿不到已经生成好的答案。
    """
    from app.repositories import agent_log_repository

    with session_factory() as db:
        monkeypatch.setattr(
            db, "flush",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        got = agent_log_repository.create(
            db, user_id=1, query="q", answer="a", hit=True, source_count=1,
            iterations=1, steps=[], total_tokens=1, elapsed_ms=1,
        )
    assert got is None


def test_repository_does_not_finish_the_transaction(session_factory, monkeypatch):
    """repository 只 flush，不 commit —— 事务边界归 service 层。

    实测口径：其余 7 个 repository 的 db.commit() 计数全是 0，只有这里曾经是 1。
    如果它提前 commit，将来"messages + agent_logs 同一事务"会被截断成部分提交，
    而破坏点藏在一个看起来人畜无害的 repo 函数里。
    """
    from app.repositories import agent_log_repository

    with session_factory() as db:
        calls = {"commit": 0, "flush": 0}
        monkeypatch.setattr(db, "commit", lambda: calls.__setitem__("commit", calls["commit"] + 1))
        monkeypatch.setattr(db, "flush", lambda: calls.__setitem__("flush", calls["flush"] + 1))

        got = agent_log_repository.create(
            db, user_id=1, query="q", answer="a", hit=True, source_count=1,
            iterations=1, steps=[], total_tokens=1, elapsed_ms=1,
        )

    assert got is not None
    assert calls["flush"] == 1, "repo 应当只 flush"
    assert calls["commit"] == 0, "repository 不应自己结束事务"
