"""长对话摘要压缩的测试（Phase 12）。

盯的是工程行为，不是"摘要写得好不好"：
  开关关着时是不是**什么都没发生**？什么时候才生成？生成的是不是**增量**？
  摘要到底进没进提示词？生成失败会不会把这一轮问答也带走？

外部依赖全部替掉（检索 + LLM），一次真实 API 都不打。
"""

import pytest

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models import Conversation
from app.rag import llm as llm_module
from app.rag import prompt
from app.services import conversation_service, rag_service, vector_service


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
    """替掉检索与 LLM。分两路记：普通生成进 `chat`，摘要调用进 `summaries`。"""
    box: dict = {"results": [_hit()], "chat": [], "summaries": [], "summary_error": False}

    def fake_search(db, user, query, kb_ids=None, top_k=5, rerank=False):
        return {
            "query": query, "top_k": top_k, "searched_kb_ids": [1],
            "results": box["results"], "reranked": False, "candidate_k": top_k,
        }

    def fake_chat(system, user, max_tokens=None, temperature=None, history=None):
        if system == prompt.SUMMARY_SYSTEM:
            if box["summary_error"]:
                raise AppException(ErrorCode.LLM_ERROR, "摘要模型调用失败")
            box["summaries"].append(user)
            return {
                "answer": "摘要 v%d" % len(box["summaries"]),
                "usage": {"total_tokens": 1},
                "finish_reason": "stop",
            }
        box["chat"].append({"system": system, "user": user, "history": history})
        return {
            "answer": "答 %d" % len(box["chat"]),
            "usage": {"total_tokens": 1},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(vector_service, "search", fake_search)
    monkeypatch.setattr(llm_module, "chat", fake_chat)
    return box


def _ask(client, user, query: str, **extra) -> dict:
    body = client.post(
        "/api/chat", json={"query": query, **extra}, headers=user["headers"]
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def _summary_settings(monkeypatch, *, enabled: bool = True, window: int = 1, trigger: int = 2):
    """把窗口与触发阈值压小，几条用例就能触发一次压缩。"""
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", enabled)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", window)
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_TRIGGER_TURNS", trigger)


# ==================== 纯函数 ====================


def test_build_summary_section_is_pure():
    """空摘要 / 预算 0 → 空串；超长时**按尾部截断**（保留更近的那部分）。"""
    assert prompt.build_summary_section(None, 100) == ""
    assert prompt.build_summary_section("   ", 100) == ""
    assert prompt.build_summary_section("聊过年假", 0) == ""

    section = prompt.build_summary_section("聊过年假", 100)
    assert section.startswith(prompt.SUMMARY_HEADER) and "聊过年假" in section

    # 与 build_context / build_memory_section 相反：这里保留**结尾**。
    # 断言只看正文（表头里本身就有"较早"，不能拿整段判）。
    long_text = "早" * 50 + "近" * 50
    section = prompt.build_summary_section(long_text, 20)
    body = section.split("\n", 1)[1]
    assert body == "…" + "近" * 20, body


def test_system_prompt_composition_order():
    """摘要在前、记忆在后；两个都没有时**逐字**等于原始系统提示词。"""
    assert prompt.build_system_prompt() == prompt.SYSTEM_PROMPT
    assert prompt.build_system_prompt("", "") == prompt.SYSTEM_PROMPT

    both = prompt.build_system_prompt("【记忆】", "【摘要】")
    assert both.index("【摘要】") < both.index("【记忆】"), "记忆应当更接近问题（排在后面）"


# ==================== 开关与生成时机 ====================


def test_summary_off_by_default_changes_nothing(client, make_user, chat_env, monkeypatch, session_factory):
    """默认关：不生成摘要、也不注入 —— 提示词逐字等于从前。"""
    _summary_settings(monkeypatch, enabled=False)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 6):
        _ask(client, admin, f"第{i}问", conversation_id=conv_id)

    assert chat_env["summaries"] == [], "开关关着却调了摘要模型"
    assert chat_env["chat"][-1]["system"] == prompt.SYSTEM_PROMPT
    with session_factory() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.summary is None and conv.summary_upto_id is None


def test_summary_is_generated_then_injected(client, make_user, chat_env, monkeypatch, session_factory):
    """攒够触发轮数就压缩一次，并从**下一轮**开始出现在提示词里。"""
    _summary_settings(monkeypatch, enabled=True, window=1, trigger=2)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 5):
        _ask(client, admin, f"第{i}问", conversation_id=conv_id)

    # 窗口 1 轮：窗口外攒到 2 轮时压缩一次（第 1、2 问那两轮）
    assert len(chat_env["summaries"]) == 1, chat_env["summaries"]
    assert "第1问" in chat_env["summaries"][0], "第一次摘要应当覆盖最早的轮次"

    with session_factory() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.summary == "摘要 v1"
        assert conv.summary_upto_id is not None, "必须记住覆盖到哪条，否则每轮都会重头总结"

    # 摘要在"第3轮追加时写出"，所以第4轮（以及更晚）的提示词里才带上它
    last_system = chat_env["chat"][-1]["system"]
    assert last_system.startswith(prompt.SYSTEM_PROMPT)
    assert "摘要 v1" in last_system
    # 窗口仍然只有最近 1 轮 —— 摘要替代的是**窗口之外**的历史，不是挤掉窗口
    assert chat_env["chat"][-1]["history"] == [("user", "第3问"), ("assistant", "答 3")]


def test_summary_is_incremental(client, make_user, chat_env, monkeypatch, session_factory):
    """第二次压缩只喂**新滑出窗口**的那几轮，不重头总结一遍。

    这是这个功能成本可控的关键：每轮重喂整段对话，token 会随轮数平方级上涨。
    """
    _summary_settings(monkeypatch, enabled=True, window=1, trigger=2)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 7):
        _ask(client, admin, f"第{i}问", conversation_id=conv_id)

    assert len(chat_env["summaries"]) >= 2, chat_env["summaries"]
    second = chat_env["summaries"][1]
    assert "第1问" not in second, "第二次摘要又喂了已经覆盖过的轮次"
    assert "第3问" in second, "第二次摘要应当喂新滑出窗口的那几轮"

    # 第二次调用要把「已有摘要」带上，否则合并出来的摘要会丢掉前半段
    assert "摘要 v1" in second


def test_summary_failure_does_not_break_the_turn(client, make_user, chat_env, monkeypatch, session_factory):
    """摘要模型挂了：这一轮问答照常完成，字段保持原样（下一轮还会再试）。"""
    _summary_settings(monkeypatch, enabled=True, window=1, trigger=2)
    admin = make_user("admin")
    chat_env["summary_error"] = True

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 5):
        data = _ask(client, admin, f"第{i}问", conversation_id=conv_id)
        assert data["answer"], "摘要失败把这一轮问答也带走了"

    with session_factory() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.summary is None, "失败时不该写半个摘要进去"
        assert conv.summary_upto_id is None, "失败时不该推进覆盖位置（否则那几轮就永久丢了）"


def test_summary_requires_history_window(client, make_user, chat_env, monkeypatch):
    """`CONVERSATION_MAX_HISTORY_TURNS=0`（不带历史）时不做摘要 —— 摘要唯一的作用就是替窗口外的轮次留底。"""
    _summary_settings(monkeypatch, enabled=True, window=0, trigger=1)
    admin = make_user("admin")

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 5):
        _ask(client, admin, f"第{i}问", conversation_id=conv_id)

    assert chat_env["summaries"] == [], "不带历史时摘要没有意义，不该白调模型"


def test_summary_section_respects_switch(client, make_user, monkeypatch):
    """`summary_section` 是对外唯一入口：开关关时一律空串（与记忆段同一个口径）。

    用 `monkeypatch` 而不是直接写 `settings.X = …`：后者不会自动还原，
    而且本文件下面那句 `finally` 写死的是**字面量 False** —— 哪天默认值翻成 True，
    这条用例就会把之后所有用例的开关留在 False（顺序一变，全文件跟着红）。
    """
    from types import SimpleNamespace

    conv = SimpleNamespace(summary="聊过年假")

    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", False)
    assert conversation_service.summary_section(conv) == ""

    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", True)
    assert "聊过年假" in conversation_service.summary_section(conv)


# ==================== 顺序与配置（阶段 12 补）====================


def test_summary_is_refreshed_before_trimming(client, make_user, chat_env, monkeypatch):
    """`append_turn` 里**摘要必须先于裁剪**（白盒顺序守卫）。

    这两步的先后是拿数据安全换来的：裁剪会**永久删掉**超出上限的最旧消息，
    而摘要是那些轮次唯一的底。反过来的顺序，会让"还没进摘要就被删"的轮次彻底消失
    —— 且不报错、不打日志，只有用户某天发现"早先聊的内容查不到了"。

    这里直接钉调用序，不绕道业务断言：**只要有人把两行调换，这条立刻红**。
    """
    calls: list[str] = []
    real_summary = conversation_service.maybe_refresh_summary
    real_trim = conversation_service._trim_messages

    def spy_summary(db, conv):
        calls.append("summary")
        return real_summary(db, conv)

    def spy_trim(db, conv):
        calls.append("trim")
        return real_trim(db, conv)

    monkeypatch.setattr(conversation_service, "maybe_refresh_summary", spy_summary)
    monkeypatch.setattr(conversation_service, "_trim_messages", spy_trim)

    admin = make_user("admin")
    _ask(client, admin, "问一句")

    assert calls == ["summary", "trim"], f"摘要与裁剪的先后被调换了：{calls}"


def test_summary_runs_before_trim_so_nothing_is_lost(
    client, make_user, chat_env, monkeypatch
):
    """**数据回归**：一次删掉一大批旧消息时，它们必须先进过摘要。

    构造的是**积压**场景（比单纯调小上限更接近真实）：先按默认上限正常聊 6 轮，
    此时摘要关着、消息只积压不清；然后打开摘要并把上限压到"恰好合法"的最小值
    `(window + trigger) * 2`。上限一压小，下一轮会一次删掉 8 条 ——
    如果裁剪抢在摘要前面，最早那几轮在摘要见到它们之前就被删了。

    断言取"摘要输入里有没有『第1问』"：最早的轮次是**最容易被先删掉**的那个，
    它出现了，说明裁剪删的都在摘要覆盖范围内。
    """
    admin = make_user("admin")
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", False)

    conv_id = _ask(client, admin, "第1问")["conversation_id"]
    for i in range(2, 7):
        _ask(client, admin, "第%d问" % i, conversation_id=conv_id)

    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_HISTORY_TURNS", 1)
    monkeypatch.setattr(settings, "CONVERSATION_SUMMARY_TRIGGER_TURNS", 2)
    # (1 + 2) * 2 = 6：刚好满足 config 的交叉校验，同时让裁剪一次删掉一大批
    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 6)

    _ask(client, admin, "第7问", conversation_id=conv_id)

    assert chat_env["summaries"], "一批积压的旧轮次滑出窗口后，摘要应当被触发"
    assert "第1问" in chat_env["summaries"][0], (
        "最早那一轮没能进摘要输入 —— 说明裁剪抢在摘要前面把它删了"
    )


def test_summary_requires_room_to_accumulate():
    """上限太小会让摘要**永远攒不够输入**，而裁剪照常删 —— 必须挡在启动时。

    这是个静默失效：摘要一次都不生成、日志里也没有报错，只表现为"聊得越久、
    早期的内容越查不到"。判据是 `cap >= (window + trigger) * 2`（一轮 = 2 条）。
    """
    from app.config import Settings

    # 恰好合法：(1 + 2) * 2 = 6
    ok = Settings(
        CONVERSATION_SUMMARY_ENABLED=True,
        CONVERSATION_MAX_HISTORY_TURNS=1,
        CONVERSATION_SUMMARY_TRIGGER_TURNS=2,
        CONVERSATION_MAX_MESSAGES=6,
    )
    assert ok.CONVERSATION_MAX_MESSAGES == 6

    with pytest.raises(ValueError, match="CONVERSATION_MAX_MESSAGES"):
        Settings(
            CONVERSATION_SUMMARY_ENABLED=True,
            CONVERSATION_MAX_HISTORY_TURNS=5,
            CONVERSATION_SUMMARY_TRIGGER_TURNS=10,
            CONVERSATION_MAX_MESSAGES=20,  # (5 + 10) * 2 = 30 > 20
        )

    # 三种"没有追不上这回事"的情形都不该被拦：关摘要、不限存量、不带历史
    Settings(CONVERSATION_SUMMARY_ENABLED=False, CONVERSATION_MAX_MESSAGES=20)
    Settings(CONVERSATION_SUMMARY_ENABLED=True, CONVERSATION_MAX_MESSAGES=0)
    Settings(CONVERSATION_SUMMARY_ENABLED=True, CONVERSATION_MAX_HISTORY_TURNS=0)
