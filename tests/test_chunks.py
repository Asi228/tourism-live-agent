"""引用溯源：点答案里的 `[n]` 看完整原文。

数据直接用 `session_factory` 造（不走上传 + 后台解析）—— 这一组测的是
**溯源接口的权限与返回形状**，把文档处理那一整套异步流程牵进来只会让它变脆。
"""

import uuid

from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import (
    STATUS_COMPLETED,
    Document,
    DocumentChunk,
    KnowledgeBase,
)

FULL_TEXT = "员工入职满一年可享受带薪年假 5 天；满三年 10 天；满五年 15 天。"


def _seed_chunk(session_factory, owner_id):
    """造「知识库 → 文档 → 一个 chunk」并返回 (kb_id, doc_id, chunk_id)。"""
    with session_factory() as db:
        kb = KnowledgeBase(
            name="kb-" + uuid.uuid4().hex[:10], description="测试", owner_id=owner_id
        )
        db.add(kb)
        db.flush()
        doc = Document(
            knowledge_base_id=kb.id,
            uploader_id=owner_id,
            filename="员工考勤与休假管理制度.md",
            stored_name="stored.md",
            file_path="/tmp/stored.md",
            file_type="md",
            file_size=len(FULL_TEXT.encode("utf-8")),
            status=STATUS_COMPLETED,
            chunk_count=1,
            char_count=len(FULL_TEXT),
        )
        db.add(doc)
        db.flush()
        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            content=FULL_TEXT,
            page_number=3,
            char_count=len(FULL_TEXT),
        )
        db.add(chunk)
        db.commit()
        return kb.id, doc.id, chunk.id


def test_returns_full_content_not_snippet(client, make_user, session_factory):
    """🔴 核心：返回的是**完整**正文，不是问答里那段截断的 snippet。"""
    admin = make_user("admin")
    _, doc_id, chunk_id = _seed_chunk(session_factory, admin["id"])

    res = client.get(f"/api/chunks/{chunk_id}", headers=admin["headers"]).json()
    assert res["code"] == 0, res
    data = res["data"]
    assert data["content"] == FULL_TEXT
    assert data["document_id"] == doc_id
    assert data["document_name"] == "员工考勤与休假管理制度.md"
    assert data["page_number"] == 3
    assert data["char_count"] == len(FULL_TEXT)
    assert data["chunk_index"] == 0
    # 前端靠它判断要不要弹"属于哪个库"，不返回的话还得再查一次
    assert "knowledge_base_id" in data


def test_member_with_read_permission_can_see_it(client, make_user, session_factory):
    """库级 read 授权也能溯源 —— 不是只有库主能看。"""
    admin, employee = make_user("admin"), make_user()
    kb_id, _, chunk_id = _seed_chunk(session_factory, admin["id"])

    assert client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": employee["id"], "permission": "read"},
        headers=admin["headers"],
    ).json()["code"] == 0

    got = client.get(f"/api/chunks/{chunk_id}", headers=employee["headers"]).json()
    assert got["code"] == 0, got
    assert got["data"]["content"] == FULL_TEXT


def test_user_without_permission_is_not_found(client, make_user, session_factory):
    """没权限一律 404 —— 403 等于确认"这个 chunk_id 存在"。"""
    admin, other = make_user("admin"), make_user()
    _, _, chunk_id = _seed_chunk(session_factory, admin["id"])

    got = client.get(f"/api/chunks/{chunk_id}", headers=other["headers"]).json()
    assert got["code"] == ErrorCode.NOT_FOUND, got


def test_unknown_chunk_is_not_found(client, make_user):
    u = make_user()
    assert client.get("/api/chunks/999999", headers=u["headers"]).json()[
        "code"
    ] == ErrorCode.NOT_FOUND


def test_requires_auth(client):
    assert client.get("/api/chunks/1").json()["code"] == ErrorCode.UNAUTHORIZED


def test_chunk_survives_in_db_for_tracing(client, make_user, session_factory):
    """溯源的前提：正文确实存在 MySQL（Milvus 里只有向量）。"""
    admin = make_user("admin")
    _, doc_id, chunk_id = _seed_chunk(session_factory, admin["id"])

    with session_factory() as db:
        rows = list(
            db.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
            )
        )
    assert [r.id for r in rows] == [chunk_id]
    assert rows[0].content == FULL_TEXT
