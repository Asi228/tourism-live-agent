"""回归测试：专门守住被评审抓出来的那几条 bug。

放一个独立文件而不是散进各自模块，理由是这些用例的**判据都是"曾经错过的那一点"**，
而不只是"这个接口正常工作"。每条的 docstring 都写了"错在哪、错了会怎样"——
哪天有人想简化掉它，得先看见代价。

⚠️ 这里覆盖不到的：`MemoryPanel` 的开关、`Chat.vue` 的 `answer_mode` 接线与
会话切换竞态 —— 三条都是**纯前端**逻辑，本项目还没有前端测试栈。
它们只能靠人工验，或者等 vitest 装上。别以为"全绿 = 这几条也好着"。
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import KnowledgeBaseFaq


def _make_kb(client, headers, **extra) -> int:
    payload = {"name": "kb-" + uuid.uuid4().hex[:10], "description": "测试", **extra}
    r = client.post("/api/knowledge", json=payload, headers=headers)
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


# ==================== ① PUT 传显式 null 不该 500 ====================


def test_put_explicit_null_on_not_null_field_is_ignored(client, make_user):
    """`{"name": null}` 语义等同"不改这一项"，不是"把名字设成 NULL"。

    错在哪：pydantic 的 `exclude_unset=True` **只挡"没传"，挡不住"显式传 null"**
    —— 传了就算 set。于是一个 `{"name": null}` 会一路走到 `setattr(kb, "name", None)`，
    到 commit 才炸（59999），从报错完全看不出是哪里。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"], name="原始名称")

    res = client.put(
        f"/api/knowledge/{kb_id}", json={"name": None}, headers=admin["headers"]
    ).json()
    assert res["code"] == 0, res
    assert res["data"]["name"] == "原始名称", "null 被当成新值写进去了"


def test_put_explicit_null_on_is_public_is_ignored(client, make_user):
    """`is_public` 也是 NOT NULL —— 同一个坑的第二列。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    assert client.put(
        f"/api/knowledge/{kb_id}", json={"is_public": True}, headers=admin["headers"]
    ).json()["data"]["is_public"] is True

    res = client.put(
        f"/api/knowledge/{kb_id}", json={"is_public": None}, headers=admin["headers"]
    ).json()
    assert res["code"] == 0, res
    assert res["data"]["is_public"] is True, "null 把权限开关改掉了"


def test_put_null_description_still_clears_it(client, make_user):
    """🔴 反面：`description` **是** nullable 的 —— 传 null 表示"清空描述"，
    不能被上面那条修复顺手一起剔掉。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"], description="原来的描述")
    assert client.get(f"/api/knowledge/{kb_id}", headers=admin["headers"]).json()[
        "data"
    ]["description"] == "原来的描述"

    res = client.put(
        f"/api/knowledge/{kb_id}", json={"description": None}, headers=admin["headers"]
    ).json()
    assert res["code"] == 0, res
    assert res["data"]["description"] is None, "清空描述被误伤了"


def test_put_null_alone_does_not_touch_anything_else(client, make_user):
    """只传 null 时不该连带改别的字段。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"], name="名字不动", description="描述不动")

    client.put(
        f"/api/knowledge/{kb_id}",
        json={"name": None, "description": None, "is_public": None},
        headers=admin["headers"],
    )
    got = client.get(f"/api/knowledge/{kb_id}", headers=admin["headers"]).json()["data"]
    assert got["name"] == "名字不动"
    assert got["is_public"] is False


# ==================== ② Agent 流式的配额错误要能走统一信封 ====================


def test_agent_stream_quota_error_returns_envelope_not_stream(
    client, make_user, monkeypatch, session_factory
):
    """会话数到顶时，Agent 流式必须返回**统一信封**，而不是一个 200 流里的事件。

    错在哪：`stream_answer` 当初只做了归属校验，而含配额检查的
    `ensure_conversation` 在生成器里 —— 响应头已经发出去了，40900 只能降级成
    `event: error`。前端把它当"断流"，用户根本不知道是自己配额满了。
    RAG 那条路一直是对的，这条是补齐。

    ⚠️ 名额**直接写库**占满，不走问答链路 —— 否则"第一问是否真的建出了会话"
    会依赖 LLM 替身有没有配，测试就变成了碰运气。
    """
    from app.config import settings
    from app.models import Conversation

    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_PER_USER", 1)
    u = make_user()

    with session_factory() as db:
        db.add(Conversation(user_id=u["id"], title="占位"))
        db.commit()

    res = client.post(
        "/api/agent/chat/stream", json={"query": "再问一句"}, headers=u["headers"]
    )
    assert "text/event-stream" not in res.headers.get("content-type", ""), (
        "配额到顶竟然返回了 SSE —— 40900 会降级成流内事件，用户看到的是断流"
    )
    assert res.json()["code"] == ErrorCode.RESOURCE_LIMIT_EXCEEDED, res.json()


def test_agent_stream_with_existing_conversation_checks_ownership_not_quota(
    client, make_user, monkeypatch
):
    """带着 conversation_id 时不走配额分支 —— 校验的是**归属**（404），不是配额。

    这条守住"补救配额检查时别顺手把'接着聊'也拦掉"。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "CONVERSATION_MAX_PER_USER", 1)
    u = make_user()

    res = client.post(
        "/api/agent/chat/stream",
        json={"query": "继续问", "conversation_id": 999999},
        headers=u["headers"],
    )
    # 不存在的会话 → 404（归属校验），**不是** 40900（配额）
    assert res.json()["code"] == ErrorCode.NOT_FOUND, res.json()


# ==================== ③ 删库要连带删 FAQ（ORM 级联）====================


def test_delete_kb_cascades_faqs(client, make_user, session_factory):
    """删知识库必须连带删掉它的 FAQ —— 靠 ORM 级联，不靠 SQLite 的外键开关。

    错在哪：FAQ 表当初只写了数据库侧的 `ON DELETE CASCADE`，漏了 ORM 这一路。
    而 SQLite（测试库）默认不开外键约束 → 测试里 `delete_kb` 会留下孤儿 FAQ，
    生产库（MySQL）却会级联删 —— 两边行为不一致，测试看不见这个差异。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    assert client.post(
        "/api/faqs",
        json={"knowledge_base_id": kb_id, "question": "年假几天", "answer": "5 天"},
        headers=admin["headers"],
    ).json()["code"] == 0
    with session_factory() as db:
        assert db.scalar(select(KnowledgeBaseFaq)) is not None

    assert client.delete(
        f"/api/knowledge/{kb_id}", headers=admin["headers"]
    ).json()["code"] == 0

    with session_factory() as db:
        left = list(db.scalars(select(KnowledgeBaseFaq)))
    assert left == [], f"删库后还留着 {len(left)} 条孤儿 FAQ"


# ==================== ④ 错误信息不许泄露服务端路径 ====================


def test_loader_error_does_not_leak_path(tmp_path):
    """文件读取失败时，回给客户端的话里**不能有路径**。

    错在哪：`f"文件读取失败：{e}"` 把 `OSError` 原文拼进 message，而它不是只进日志
    —— 它会经 document_service 落库、再由接口回给客户端，等于把服务端目录结构
    送给用户。与项目"上游原文只进日志"的既定口径直接冲突。

    用一个**目录**当输入来制造 `OSError`（`IsADirectoryError` 是它的子类），
    不用真的去弄坏磁盘。
    """
    from app.core.exceptions import AppException
    from app.rag import loader

    target = tmp_path / "其实是个目录.md"
    target.mkdir()

    with pytest.raises(AppException) as ei:
        loader._load_text(target)

    msg = ei.value.message
    assert str(target) not in msg, f"路径泄露了: {msg!r}"
    assert str(tmp_path) not in msg, f"目录泄露了: {msg!r}"
    assert "读取失败" in msg  # 但人话还在，不是把 message 变成空的


# ⚠️ `file_storage.py` 的落盘失败那条**没有自动化用例**：它在一个 `async def` 里，
# 要构造出真实 OSError 得把 UploadFile 和 aiofiles 都替身一遍，成本高于收益。
# 那条改动的正确性靠代码评审 + 与上面 `_load_text` 完全相同的写法来保证。
# 别以为"这里全绿 = 上传路径也不泄露"。
