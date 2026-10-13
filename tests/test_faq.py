"""知识库 FAQ（常见问题置顶）。

测三件事：权限口径（读=read / 写=admin）、上限、以及"它不参与检索"这条边界。
"""

import uuid

from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import FAQ_MAX_PER_KB, KnowledgeBase, KnowledgeBaseFaq


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
